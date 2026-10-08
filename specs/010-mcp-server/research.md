# Research: MCP Server (spec 010)

Findings from reading the code on 2026-10-06. Every "Decision" is closed unless marked **OPEN**.

## R1 — Hosting: in-process, mounted in the existing FastAPI app
- **Decision**: Mount the MCP endpoint at `/mcp` inside the existing app (Streamable HTTP transport).
- **Rationale**: Reuses `app.state.influx`, the station registries, config, logging, Traefik routing and the
  Docker image. A separate process would need its own Influx client, registry priming and deploy path.
- **Alternatives**: separate service (rejected: duplicate wiring, second deploy); stdio-only (rejected:
  not reachable from remote assistants).

## R2 — SDK
- **Decision**: Official Python MCP SDK (`mcp`, `FastMCP` API), configured `stateless_http=True` and
  `json_response=True`.
- **Rationale**: Stateless + JSON responses means no session store and **no SSE streaming** — which matters
  because the app wraps everything in `GZipMiddleware` and a `BaseHTTPMiddleware` (`main.py:377-378`), both
  historically hostile to long-lived event streams. Read-only tools need neither.
- **Risk / spike (T001)**: confirm `mcp` resolves under the pinned `fastapi (<0.125)`, `pydantic`, `httpx (<0.29)`
  ranges with `poetry lock`. If it conflicts, fall back to hand-implementing the JSON-RPC subset
  (initialize, tools/list, tools/call) — small for a stateless tools-only server.
- **Lifespan caveat**: a mounted sub-app's lifespan is not run by Starlette; the SDK's session manager must be
  entered from the main `lifespan` in `main.py`.

## R3 — Tools call the data layer directly, not HTTP
- **Decision**: Tool functions call `InfluxClient` and the registries (same as the routers do). No self-HTTP
  calls, no refactor of existing routers.
- **Rationale**: Minimal-scope rule (`00-ai-usage.md`). Shared logic that already lives in helper modules
  (`foehn_detection`, `services/dedup`) is imported; logic embedded in routers (`_evaluate` in `foehn.py`,
  `_require_known_station`) is either imported or re-expressed in ~5 lines.

## R4 — "Verified" enforcement: allowlist + a MCP-private registry (the non-obvious one)
- **Facts**: networks in code: `meteoswiss, slf, metar, holfuy, windline, fga, jfb, foehn` (verified) and
  `wunderground, ecowitt` (excluded). The website registry (`app.state.station_registry`) contains all of them
  and **must stay that way** — pilots build rules on private stations.
- **Trap**: `display_registry`/`virtual_members` merge co-located stations. `query_latest_virtual(members)` and
  `query_history_virtual(members)` pool *all members* newest-wins. A cluster whose canonical station is
  MeteoSwiss but which also has an Ecowitt member would leak Ecowitt values into a "MeteoSwiss" answer.
  Filtering the final list is therefore **not enough**.
- **Decision**: At MCP startup build a **separate** registry:
  `verified_raw = {id: s for id, s in station_registry.items() if s.network in VERIFIED_NETWORKS}`, then run
  `build_deduped_registry(verified_raw, ...)` on that. All MCP tools resolve stations only through this registry
  and its own `virtual_members`. Rebuilt whenever the main registry is rebuilt (`rebuild_display_registry`,
  `_make_registry_updater` hooks — compose, never replace; hooks are single slots).
- **Allowlist, not blocklist**: config key `mcp.verified_networks` defaults to the 7 real-world networks.
  A newly added collector is hidden from MCP until consciously listed (fail closed, matching the project's
  security stance). Documented in `prompts/add-collector.md`.
- `foehn` virtual stations are excluded from station search (they are not real stations); they are served by
  the föhn tool.

## R5 — Föhn inputs
- **Fact**: `foehn_detection.py` default config references only `meteoswiss-*` and `slf-*` stations — all
  verified. The föhn config is an admin-editable JSON file, so that could change.
- **Decision**: MCP föhn tools always use the **system default config** (`user_config=None`) — never a
  caller's/pilot's config. A test asserts every station id in the default config belongs to a verified
  network; startup logs a WARNING if not.

## R6 — Existing endpoints don't fit the spec as-is
| Spec need | Existing | Gap → decision |
|---|---|---|
| Search by name/point | none (`GET /api/stations` returns all + latest) | New in-memory search over the verified registry (no Influx). Haversine from `services/dedup.py` |
| Current weather | `query_latest` / `query_latest_virtual` | Reuse; add age + stale flag in the tool layer |
| History over a date range | `query_history(hours)` only, max 720 h, raw points | **New** `InfluxClient.query_history_range(station_id, start, end, every)` using `aggregateWindow` (mean; `last` for direction? see R8) + hard point cap. Lookback `hours` form maps to it |
| Forecast + model run time | `query_forecast_for_stations` strips `init_date` | Add kwarg `keep_init_date=False`; tool reports `forecast_issued` (newest run contributing). Default unchanged → no router impact |
| Föhn status | `/api/foehn/status` logic in router | Import `eval_region`, `build_all_pressures`, `build_response` as `_evaluate` does; `/forecast` via `query_forecast_snapshot_for_stations` |
| Rate limit | none anywhere | New small in-process limiter (R7) |

## R7 — Rate limiting & client identity
- **Fact**: no limiter exists. Uvicorn runs without `--forwarded-allow-ips`; behind Traefik the TCP peer is the
  proxy container, so `request.client.host` would put **every caller in one bucket**.
- **Decision**: per-caller sliding-window limiter, keyed by the **rightmost** `X-Forwarded-For` entry (the
  address Traefik itself observed — not spoofable by a client-supplied header when exactly one proxy hop),
  falling back to `client.host`. `mcp.trusted_proxy_hops` (default 1) configures this. Bounded dict + lock +
  oldest-eviction (T10). Defaults: 60 calls/min/caller, 1000 distinct callers tracked. Limit hit → clear
  error with retry-after seconds (FR-016, edge case).
- **Alternative**: Traefik `rateLimit` middleware (IaC repo). Valid complement, but prod changes go through the
  lg4 IaC repo and the app must protect itself regardless; recommend both.

## R9 — Station-id & input validation
- IDs match `^[\w\-]{1,64}$` (T01) before reaching Flux; additionally must exist in the MCP registry. Free-text
  `query` is never interpolated into Flux (search is in-memory). Dates parsed as ISO-8601, clamped.

## R8 — Output shaping
- Units fixed by the unified schema (`models/weather.py`): wind km/h, °C, % RH, hPa, precipitation **mm**
  (owner-confirmed 2026-10-08). Tool output keys carry units in a top-level `units` map (not per-value) to keep
  payloads small; spec FR-004 is satisfied by the map being in every response.
- History cap: ≤ 500 points/response; `every` auto-picked (10 m / 30 m / 1 h / 3 h / 1 d) from range; response
  states `resolution` and `downsampled: true`. Wind direction must not be arithmetic-meaned: use vector mean
  or `last` per bucket (decision: `last`, noted in response). 
- Staleness: `age_minutes` always; `stale: true` when > 120 min (same threshold the JFB collector uses).
- Forecast: hours with every field null omitted and listed in `missing_hours` summary (count + ranges); known
  ICON-CH1/CH2 gap is therefore visible, not hidden. `forecast_gap_note` static text.
- Suppressed fields: map `SUPPRESSED_FIELDS = {"jfb-hollandiahutte-sac": {temperature, pressure_qfe, pressure_qff}}`
  applied in one choke point (`mcp/sanitize.py`) used by current + history + forecast. Suppressed set for that station:
  temperature, humidity, pressure_qfe, pressure_qff (humidity added by owner decision 2026-10-08).

## R10 — Holfuy terms
- **Decision (owner, 2026-10-08)**: assume redistribution is allowed (winds.mobi redistributes it). Holfuy stays
  in `verified_networks`. If that changes, remove it from config — no code change. Record the assumption in
  `security-notes.md` when documenting.

## R11 — Auth later (FR-017)
- Public tools live in one module; the server is created through a factory taking an `auth` policy. Phase 1 =
  anonymous. A later spec adds a second mount (e.g. `/mcp/pilot`) with bearer-token auth and rule-set tools,
  so the public tool contracts never change meaning. Nothing in phase 1 stores users or tokens.

## R12 — Browser/CORS
- MCP clients are not browsers (except in-browser MCP clients). CORS stays disabled (existing comment says never
  `*`); CSP headers are irrelevant to JSON. Revisit only if a browser-based MCP client is a requirement.
