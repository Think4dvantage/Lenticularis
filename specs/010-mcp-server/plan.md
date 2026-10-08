# Implementation Plan: Public Weather Access for AI Assistants (MCP Server)

Spec: [spec.md](spec.md) · Research: [research.md](research.md) · Model: [data-model.md](data-model.md) ·
Contracts: [contracts/mcp-tools.md](contracts/mcp-tools.md)

## Revisions after advisor review & Phase 0 spike (2026-10-08) — these OVERRIDE the text below

**Phase 0 spike results** (`mcp` SDK, installed and exercised against the real FastAPI + GZip + BaseHTTPMiddleware stack):
- `mcp` **2.x** (released; renames `FastMCP`→`MCPServer`, pulls `httpx2`) is new and breaking → **pin `mcp>=1.30,<2`**
  (v1.30.0 resolves with no change to pinned fastapi 0.124 / starlette 0.50 / pydantic 2.12 / httpx 0.28).
- `mcp.streamable_http_app()` mounted at `/mcp` answers **`307 → http://…/mcp/`** for `POST /mcp` — a downgraded
  scheme behind Traefik (uvicorn does not trust `X-Forwarded-Proto`). **Fix**: do not use `Mount`; register explicit
  `Route`s for `/mcp` and `/mcp/` that forward to the sub-app with `path="/"` (`streamable_http_path="/"`).
  Exit gate: `POST /mcp` without slash → 200, no redirect.
- DNS-rebinding protection is **on by default** and returns `421 Invalid Host header` for unlisted hosts →
  set `allowed_hosts` explicitly from config (`mcp.allowed_hosts`, default `[lenti.cloud, lenti.sdh.lol, lenti-dev.lg4.ch, localhost, 127.0.0.1, test]`; hosts incl. `:port` handled).
- **Constructing `FastMCP` reconfigures the root logger** (handlers changed) → snapshot/restore root handlers+level around construction; test asserts unchanged.
- `session_manager.run()` works once per instance → FastMCP instance is created **per `create_app()`**, `run()` entered in main lifespan.
  `ASGITransport` tests do not run lifespan → tool functions are unit-tested directly; one integration test enters `run()` manually.
- **Lockfile**: `poetry` is not installed locally and the Dockerfile requires a strict, in-sync `poetry.lock`.
  Regenerate in a scratch copy with a temporary poetry venv; if impossible, STOP and report (do not ship an out-of-sync lock).

**Design changes**
1. Package is **`src/lenticularis/mcp_server/`** (not `mcp/`) — cannot shadow the `mcp` dependency.
2. **History aggregation is per field**, stated in the response: `wind_gust`→max, `precipitation`→sum,
   `wind_direction`→last, `snow_depth`→last, all others→mean. (Gust mean would understate peaks — safety.)
3. **Föhn inputs filtered at runtime**: the default config can be edited live via `PUT /api/foehn/config?set_as_default=true`.
   The tool drops any input station whose network ∉ verified before evaluating (it then counts as no data).
   Startup warning + test remain.
4. **Manual dedup pairs**: `build_deduped_registry` already ignores pair ids not in its input (checked) — pairs are
   passed through unchanged. The MCP registry is rebuilt **directly** inside `rebuild_display_registry` and
   `_make_registry_updater` (no extra hook).
5. **History/latest for merged stations**: use only the **verified member ids** of the MCP registry's own
   `virtual_members` (never the website's). `query_history_range` takes `member_ids: list[str]`.
6. Rate-limit is enforced **inside the tool layer** (reads headers from the request context) so the error is an
   LLM-visible tool error with retry seconds; callers hashed with a **random per-process salt**; log XFF entry count once.
7. Unexpected exceptions in tools → `logger.exception` + generic `INTERNAL_ERROR` (never leak Flux/hostnames).
   Documented: Influx readers return `{}` on failure, so an outage reads as "no data".
8. Station search is **accent/umlaut-insensitive** (ü/ue/u, é/e …).
9. SC-3 website comparison excludes clusters whose website canonical is a private-network station
   (`jfb` ranks below ecowitt/wunderground in `NETWORK_PRIORITY`, so ids/values differ by design).

## Technical Context

- **Stack**: existing — Python 3.11, FastAPI, InfluxDB 2.x, Pydantic v2. One new runtime dependency: the official
  `mcp` SDK (T001 spike confirms it resolves; fallback = hand-rolled JSON-RPC subset).
- **Architecture**: new package `src/lenticularis/mcp/` mounted at `/mcp` in `create_app()`. Tools read the same
  `InfluxClient` and registries as the routers but go through an **MCP-private verified registry** (research R4).
  Stateless HTTP + JSON responses; no sessions, no SSE.
- **Security posture**: anonymous read-only ⇒ the whole attack surface is input validation, data allowlisting,
  and abuse limits. No DB writes anywhere in `mcp/`; no import of rule-set / user models.
- **Performance**: tools are single-station and bounded. Hot paths use existing batch/fast clients; history
  range uses the 10 s client with `aggregateWindow`; forecast reuses the (already optimised) horizon reader.
  Station search is pure in-memory. Blocking Influx calls wrapped in `asyncio.to_thread`.

## Constitution Check (`00-ai-usage.md`)

| Principle | Status |
|---|---|
| Read before acting | ✅ `.ai/` + routers/influx/main read before planning |
| Plan before building | ✅ this plan; implementation waits for explicit "go ahead" (planning-mode rule) |
| Minimal scope | ✅ no router refactor; one new Influx read method + one kwarg with unchanged default; no thermal/aloft/rulesets |
| Tool-agnostic `.ai/` | ✅ no `CLAUDE.md`/tool files |
| Docs in sync | ✅ T-tasks update `architecture.md`, `features.md`, `backend-notes.md`, `security-notes.md`, `README`, `add-collector.md` |
| No secrets committed | ✅ no secrets introduced |
| Prod off-limits | ✅ deploy via tag → ghcr; Traefik rate-limit complement goes via lg4 IaC |
| Hard rules (`04-constraints.md`) | ✅ `get_config()` only; logging not print; bounded locked caches; `to_thread`; Flux-injection allowlist; no os.environ; no npm |

No unresolved violations.

## File Structure

New:
```
src/lenticularis/mcp/__init__.py
src/lenticularis/mcp/server.py       # FastMCP factory, tool registration, lifespan hook, mount helper
src/lenticularis/mcp/tools.py        # the 6 tool functions (thin; validation → data → shape)
src/lenticularis/mcp/registry.py     # verified registry build/rebuild (R4), station search
src/lenticularis/mcp/sanitize.py     # SUPPRESSED_FIELDS, null stripping, units map, staleness
src/lenticularis/mcp/ratelimit.py    # bounded sliding-window limiter + client-key resolution (R7)
src/lenticularis/mcp/usage.py        # per-tool counters, distinct callers, structured log line
tests/backend/test_mcp_registry.py   # verified-only, dedup-member leak, rebuild
tests/backend/test_mcp_tools.py      # each tool: shape, units, validation, errors
tests/backend/test_mcp_ratelimit.py  # window, eviction bound, XFF rightmost
tests/backend/test_mcp_invariants.py # no pilot imports; foehn default stations verified; suppressed fields
specs/010-mcp-server/{research,data-model,plan,tasks}.md, contracts/mcp-tools.md
```
Modified:
```
src/lenticularis/api/main.py          # mount /mcp; enter MCP lifespan; build+rebuild verified registry (compose hooks)
src/lenticularis/config.py            # McpConfig + MainConfig.mcp
config.yml.example                    # mcp: block
src/lenticularis/database/influx.py   # + query_history_range(); + keep_init_date kwarg (default False)
src/lenticularis/api/routers/health.py# report mcp status/usage (operability, 08)
pyproject.toml / poetry.lock          # + mcp dependency; version bump (minor: 1.24.0)
tests/backend/conftest.py             # FakeInflux.query_history_range stub (06-testing warns about missing stubs)
README.md, .ai/context/{architecture,features,backend-notes,security-notes}.md, .ai/prompts/add-collector.md
```

## Implementation Phases

### Phase 0 — Spike & unknowns (≤ ½ day, gates everything)
1. `poetry add mcp`; confirm lock resolves and the Docker build's strict `COPY pyproject.toml poetry.lock` holds.
2. Prove stateless+JSON mount: one dummy tool reachable via `POST /mcp` through GZip + `BaseHTTPMiddleware`,
   and via an MCP client (Claude Code `claude mcp add --transport http`, MCP Inspector).
3. Confirm the other fields' units in `models/weather.py` (precipitation is mm, owner-confirmed).
**Exit gate**: dummy tool works end-to-end through the real middleware stack. If not → decide fallback (R2) before Phase 1.

### Phase 1 — Verified data foundation (the safety-critical part, built first)
Config, `mcp/registry.py` (allowlist → private dedup registry, rebuild hooks composed into
`_compose_collector_hook`/registry updater), `mcp/sanitize.py`, invariants tests. **Nothing is callable
publicly until invariants 1–3 pass.**

### Phase 2 — Core tools (P1 stories US1–US4)
`search_stations`, `get_current_weather`, `get_forecast` (with `keep_init_date`), `get_weather_history`
(with new `query_history_range`, point cap, direction handling). Error vocabulary + LLM-oriented hints.

### Phase 3 — Föhn + self-description (US5, US6)
`get_foehn_status` (live/observed/forecast via system default config; startup WARNING + test that its stations are
verified), `describe_service` + `lenticularis://about` resource.

### Phase 4 — Abuse control & observability (US7, FR-016, NFR-005)
Rate limiter + client key resolution, usage counters + one structured log line per call
(`[Lenti:mcp] tool=… ms=… ok=… caller=<hash>` — caller hashed, no IP stored), health endpoint section,
startup logging of every `mcp.*` config value.

### Phase 5 — Verify, document, ship
- Full backend suite + `ruff check --isolated`; manual client run (Claude Code + Inspector) on the 5 success-criteria questions.
- **Prod verification** (no dev env — memory: verify via read-only SSH scripts): before tagging, run the tool
  functions' logic against prod data read-only and diff against website values for a station sample (SC-3);
  confirm no ecowitt/wunderground id and no Hollandiahütte temp/pressure in any output.
- Docs sync (`sync.md`), version bump 1.24.0, tag `v1.24.0` → ghcr (user deploys; user adds Traefik rate-limit via IaC).
- Move spec to `specs/archive/` after release per `00-ai-usage.md`.

## Dependencies
- `mcp` Python SDK (new). Existing: InfluxDB, collectors, lsmfapi forecasts.
- External: MCP clients (claude.ai/Claude Code/Cursor etc.) speak Streamable HTTP; no server-side registration needed.
- Public hostname already routed by Traefik (`lenti.cloud`); `/mcp` rides the existing router rule.

## Risk & Mitigations

| Risk | Mitigation |
|---|---|
| Private-station data leaks via dedup member pooling | Private verified registry (R4) + invariant test with a synthetic cluster containing an Ecowitt member |
| New collector silently exposed | Allowlist, fail-closed; `add-collector.md` updated |
| Single-bucket rate limiting behind Traefik | Rightmost-XFF + `trusted_proxy_hops`; test; recommend Traefik limiter too |
| `mcp` dependency conflicts with pinned FastAPI/Pydantic/httpx | Phase 0 spike; hand-rolled fallback |
| SSE/streaming broken by GZip/BaseHTTPMiddleware | Stateless + `json_response`; proven in Phase 0 |
| Public load slows pilots' map/replay (NFR-001) | Per-caller limit, bounded payloads, history cap, 10 s client timeout, no per-station loops; compare `/stats` latency before/after |
| Wrong/misleading forecast gaps | Missing hours surfaced explicitly (never interpolated); note on known seam |
| Wind-direction averaging error in downsampled history | Use `last` per bucket (documented in response), never arithmetic mean |
| Föhn admin config later references an unverified station | Startup warning + test; MCP uses system default only |
| Holfuy terms disallow redistribution | Config-only removal from allowlist |
| Prompt-injection via data fields | Tool output contains only numbers/ids/known-name strings from our registry; station names come from collectors' static metadata, not user input |

## Decisions closed by owner (2026-10-08)
1. Hollandiahütte **humidity is suppressed too** (with temperature and pressure).
2. **Holfuy redistribution is assumed allowed** (winds.mobi redistributes the same data). No further check;
   if that ever changes, remove `holfuy` from `mcp.verified_networks` (config-only).
3. **Precipitation unit is mm.** Units map: wind km/h, temperature °C, humidity %, pressure hPa, precipitation mm.
   Phase 0 step 3 now only verifies the other fields' units.

No open items remain.
