# Architecture Reference

## SQLite Tables

Source of truth: `database/models.py`. **These ten tables are all of them.**

| Table | Key columns |
|---|---|
| `organizations` | `id`, `slug` (unique, indexed), `name`, `description`, `created_at` |
| `users` | `id`, `email` (unique, indexed), `display_name`, `hashed_password` (**nullable** — NULL for social-login-only accounts), `role`, `is_active`, `org_id` FK → organizations (SET NULL), `created_at`, `updated_at` |
| `oauth_identities` | `id`, `user_id` FK → users (CASCADE), `provider` (`google`/`github`), `provider_user_id`, `provider_email`, `created_at`; UNIQUE(`provider`, `provider_user_id`) |
| `rulesets` | `id`, `owner_id` FK → users (CASCADE, indexed), `name`, `description`, **`lat`, `lon`, `altitude_m`** (site identity is embedded here), `site_type` (launch/landing/opportunity), `combination_logic`, `is_public`, `is_preset`, **`is_showcase`**, `clone_count`, `cloned_from_id` FK → rulesets (SET NULL), `notify_on` (nullable CSV of colours e.g. `"green,orange"`), `last_notified_decision`, `org_id` FK → organizations (SET NULL), `created_at`, `updated_at` |
| `rule_conditions` | `id`, `ruleset_id` FK → rulesets (CASCADE, indexed), `group_id` (nullable → `condition_groups.id`), `station_id`, `station_b_id` (nullable — `pressure_delta` only), `field`, `operator`, `value_a`, `value_b` (nullable), `result_colour`, `sort_order` |
| `condition_groups` | `id`, `ruleset_id` FK → rulesets (CASCADE, indexed), `name` (**nullable** — NULL = never named), `sort_order`. Added v1.19.0 |
| `launch_landing_links` | `id`, `launch_ruleset_id` FK → rulesets (CASCADE, indexed), `landing_ruleset_id` FK → rulesets (CASCADE); UNIQUE(pair). Many-to-many; meaningful only when `site_type == "launch"` |
| `ruleset_webcams` | `id`, `ruleset_id` FK → rulesets (CASCADE, indexed), `url`, `label`, `sort_order` |
| `station_dedup_overrides` | `id`, `station_id_a`, `station_id_b`, `note`, `created_at` — manually-defined co-location pairs |
| `user_foehn_configs` | `user_id` PK FK → users (CASCADE), `config_json` (full föhn config blob), `updated_at` |

### The three publish/curate flags on `rulesets` are independent

| Flag | Whose decision | Means |
|---|---|---|
| `is_public` | **Owner** | Published — visible in the gallery, and to signed-in viewers on the map |
| `is_preset` | **Admin** | Offered as a starting template in the new-rule-set form |
| `is_showcase` | **Admin** | Curated as an example for the **anonymous** map |

Anonymous map visibility is the **read-time conjunction `is_showcase AND is_public`**. An admin
cannot showcase an unpublished rule set (409 on `set_showcase`), and an owner un-publishing hides it
immediately **without** clearing `is_showcase` — so re-publishing restores it with no admin action.
`is_public=false, is_showcase=true` is therefore a legitimate, reachable state, not a broken row.

### Foreign keys are NOT enforced

`db.py` sets `journal_mode`, `synchronous` and `busy_timeout` — but **no `PRAGMA foreign_keys=ON`**.
Every `ondelete="CASCADE"` in `models.py` is therefore documentation, not enforcement: the ORM
relationship's `cascade="all, delete-orphan"` is what actually deletes children. Any new child table
**must** declare that cascade or its rows outlive their parent forever.

### Tables that do NOT exist — do not code against them

| Assumed table | Reality |
|---|---|
| `launch_sites` | **Never existed.** Site identity (`name`, `description`, `lat`, `lon`, `altitude_m`) is embedded directly in `rulesets`. There is no `rulesets.launch_site_id`. See `models.py:113` |
| `weather_stations` | Station metadata is **not** in SQLite. The registry is built in memory by the collectors at startup and lives on `app.state.station_registry` / `app.state.display_registry` |
| `notification_configs` | Email notification state is two columns on `rulesets` (`notify_on`, `last_notified_decision`), not a separate table |

---

## InfluxDB Measurements

### `weather_data`
- **Tags**: `station_id`, `network`, `canton`
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qfe`, `pressure_qff`, `precipitation`, `snow_depth`; virtual foehn stations also have `foehn_active` (1.0=active, 0.5=partial, 0.0=inactive, −1.0=no_data)

### `weather_forecast`
- **Tags**: `station_id`, `network`, `model` (`icon-ch1`/`icon-ch2`/`open-meteo`/`icon-ch`), `source` (`swissmeteo` or `open-meteo`), `init_date` (YYYY-MM-DDTHH — one series per model-run hour)
- **Timestamp**: `valid_time` (the future moment the forecast is valid for)
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qff`, `precipitation`; SwissMeteo also writes `_min`/`_max` variants for all fields (ensemble spread); `init_time` (ISO string for Python-side dedup)
- **Primary source**: `swissmeteo` (lsmfapi ICON-CH1/CH2 ensemble). `open-meteo` is fallback.
- **Model-run selection** (all three readers, since v1.23.2): see "Forecast model-run selection is
  shared by every reader" below — `swissmeteo` outranks `open-meteo` regardless of recency, then
  newest `init_date` wins, resolved **per field** so a run's null frame is backfilled from an older
  run rather than blanking the hour. The old "prefer swissmeteo only if ≤24h fresh" staleness cutoff
  is gone — a stale `swissmeteo` run still outranks a fresher `open-meteo` one, since `open-meteo`
  is disabled in production and this only matters if it's ever re-enabled.

### `weather_forecast_thermal` (v1.22.3, `specs/006-thermal-forecast` Phase 1 — ingestion only, no rules/UI yet)
- **Tags**: `station_id`, `network`, `source` (`swissmeteo`), `model` (`icon-ch`), `init_date` (YYYY-MM-DDTHH — hour-granular, same as `weather_forecast`, **not** day-granular like `wind_forecast_grid`)
- **Timestamp**: `valid_time`
- **Fields**: 12 raw ensemble medians (`solar`, `sunshine`, `cloud_cover`, `cloud_low`, `cloud_mid`, `cloud_high`, `freezing_level`, `cape`, `cin`, `lcl`, `lfc`, `tke`); 5 selected ensemble-spread fields (`lcl_min`, `lcl_max`, `cape_max`, `cloud_cover_max`, `solar_min` — a deliberate subset, not all 24 possible `_min`/`_max`); 7 derived fields computed by `services/thermal.py` (`thermal_ceiling_m`, `cloud_base_agl_m`, `thermal_strength` 0-5, `overdevelopment_risk` 0-3, `blue_thermal` 0/1, `turbulence_index` 0-3, `ceiling_spread_m`); `init_time` (ISO string, same dedup trick as `weather_forecast`)
- **Own measurement, not merged into `weather_forecast`** — the two forecasts have different `init_time`s (separate lsmfapi cache phases), and merging would break the per-field model-run merge (see below) exactly the way `specs/006` §3.1 predicted.
- **`cin: null` means "no inhibition layer"** (ICON fill value), substituted as `0.0` in every derived calculation — never treated as a cap, and never coerced to `0` in the stored field itself (absence must stay distinguishable from a real `0`).
- **Known upstream gap**: lsmfapi sometimes nulls an entire frame range mid-horizon (observed h+8-h+33 on one run) — lands squarely on the flyable hours of "today" and "tomorrow morning". The collector logs per-hour local coverage (`coverage today=%d/12 d1=%d/12`) every run specifically because a row-count check cannot see this — rows get written either way.
- **Same 1272-cell grid as `wind_forecast_grid`** — verified in the lsmfapi source: both endpoints share one default bbox + stride and generate points with character-identical code. `thermal_forecast_grid` (Phase 3, not yet built) will join on `(grid_id, valid_time)` for a wind overlay at zero extra cost.
- Written by `collectors/forecast_thermal_swissmeteo.py` (`ForecastThermalSwissMeteoCollector`, plain class not `BaseForecastCollector` — one spatial request covers every station, same reasoning as the wind grid collector), hourly job `forecast_thermal` in `scheduler.py`. Re-collection guard skips the write when `(init_time, model, usable_frame_count)` is unchanged since the last successful run.
- **Not yet wired into the rules engine or any UI** — `FieldName`/`FIELD_MAP` extension, station-detail panel, and the `/thermal-forecast` map page are `specs/006` Phases 2-3, not started.

### `wind_forecast_grid`
- **Tags**: `grid_id` (e.g. `"47.9000_5.9000"` — 4 decimal places for lsmfapi's ICON-CH1 ~10 km grid; Open-Meteo fallback uses `"46.00_7.00"` 2 decimal places), `level_hpa` (950/900/850/800/750/700/600/500), `init_date` (YYYY-MM-DDTHH)
- **Timestamp**: `valid_time` (UTC)
- **Fields**: `wind_speed` (km/h), `wind_direction` (degrees int), `humidity` (% RH), `lat`, `lon`
- **Primary source**: `ForecastGridSwissMeteoCollector` — lsmfapi `/api/forecast/grid?level_m=X` (8 levels in parallel, 1272 grid pts, ICON-CH1). Falls back to `ForecastGridCollector` (Open-Meteo, 171 pts, 0.25° grid) if lsmfapi returns 0 wind points.
- Altitude → hPa: 500→950, 1000→900, 1500→850, 2000→800, 2500→750, 3000→700, 4000→600, 5000→500

### `rule_decisions`
- **Tags**: `ruleset_id`, `owner_id`, `site_type` — there is **no `launch_site_id` tag** (no launch-site entity exists; the ruleset *is* the site)
- **Fields**: `decision` (green/orange/red), `condition_results` (JSON array). There is **no `blocking_conditions` field** — it appears nowhere in the codebase
- Written by two paths in `rules/evaluator.py`, both emitting the identical tag/field set: `run_evaluation` (live, one point) and `write_decisions_batch` (history backfill, batched with original timestamps)

### `rule_decisions_forecast` (v1.23.0, `specs/009-reactive-ruleset-evaluation`)
- **Tags**: `ruleset_id`, `owner_id`, `site_type` — identical to `rule_decisions`
- **Timestamp**: `valid_time` (the future hour the decision applies to, **not** when it was computed)
- **Fields**: `decision` (green/orange/red), `condition_results` (JSON array) — same as `rule_decisions`
- **Deliberately a separate measurement, not merged into `rule_decisions`.** `rule_decisions` is the
  append-only *observed* history behind the org-dashboard/analysis strip. A forecast decision stored
  at a future `valid_time` would collide with the observed decision later recorded for that same
  hour — two series at one timestamp, ambiguous which the history strip should read. Same reasoning
  that kept `weather_forecast_thermal` out of `weather_forecast`.
- **Overwrite semantics**: a full-horizon rewrite, not an append. Every forecast collector run writes
  the whole horizon; identical `(tags, time)` means InfluxDB overwrites in place, so the freshest
  model run always wins with no delete step. ⚠️ **Known gap**: if a later run's horizon is *shorter*
  than an earlier one's (lsmfapi's h+8–h+33 null hole), the orphaned hours are never overwritten and
  linger stale. `weather_forecast` has the identical gap.
- Written by `scheduler.evaluate_rulesets_forecast()` via `write_decisions_batch(...,
  measurement=MEASUREMENT_DECISIONS_FORECAST)`; read by
  `InfluxClient.query_forecast_decisions_for_ruleset(ruleset_id, start, end)`.
- ⚠️ **The read query must pass an explicit `stop:`.** Flux defaults `range()`'s stop to `now()`, and
  every point in this measurement is at a *future* timestamp — omitting it returns an empty result
  with no error, silently degrading every read to the live fallback path.

**Removed**: `station_wind_profile` measurement was removed (v1.16). Altitude wind data is served by the wind forecast grid map, not per-station.

---

## Forecast Collectors

| Collector | File | Endpoint | Schedule | Notes |
|---|---|---|---|---|
| SwissMeteo surface | `forecast_swissmeteo.py` | lsmfapi `/api/forecast/station?station_id=X` | every 60 min | Primary; all stations fetched in parallel (`asyncio.gather`); flat fields + `_min`/`_max`; `init_time` + `forecast[]` schema |
| Open-Meteo surface | `forecast_openmeteo.py` | Open-Meteo API | **disabled** | Fallback only; re-enable in config if lsmfapi is unavailable for an extended period |
| SwissMeteo grid | `forecast_grid_swissmeteo.py` | lsmfapi `/api/forecast/grid?level_m=X` | every 60 min | Primary; 8 levels in parallel; 1272 pts; `ws`/`wd`/`rh` arrays |
| Open-Meteo grid | `forecast_grid.py` | Open-Meteo API | fallback only | Runs when SwissMeteo grid returns 0 wind pts |
| SwissMeteo thermal | `forecast_thermal_swissmeteo.py` | lsmfapi `/api/forecast/thermal-grid` (no params — full CH bbox, `stride_km=10`) | every 60 min | v1.22.3, `specs/006` Phase 1. One request (28.6 MB / 4.1s) → nearest-grid-point mapped onto every station. No fallback source exists. Writes `weather_forecast_thermal` (station-level) only — grid-level `thermal_forecast_grid` is Phase 3, not built |

**lsmfapi** (`lsmfapi-dev.lg4.ch`) is user-owned, same Docker network, no rate limiting. Serves ALL station networks. Response schema: `init_time`, `forecast[]` (surface) or `grid` + `frames[]` (grid). No per-station altitude wind endpoint — altitude data comes from the grid only. Updates ~4×/day (~04Z, 10Z, 16Z, 22Z); hourly collector runs are no-ops on most ticks.

**Scheduler status**: `ok_no_data` only when there were 0 eligible stations. When all stations fail with errors, status is `error`.

**`cron_hours`**: `ForecastCollectorConfig` supports optional `cron_hours: list[int]`. When set, scheduler uses `CronTrigger`; otherwise `IntervalTrigger(minutes=interval_minutes)`. Not currently used in production configs (hourly interval preferred).

---

## InfluxDB Client

Two clients in `InfluxClient.__init__()`:
- `_query_api` — `timeout` from config (default 10s) — used by all standard queries
- `_slow_query_api` — `slow_query_timeout` from config (default 60s) — used by `query_forecast_replay`,
  `query_forecast_accuracy_ranking`, and `query_forecast_snapshot_for_stations` (moved off the 10s
  default client after two observed prod timeouts under concurrent replay/forecast load — see
  `tests/backend/test_influx_query_clients.py`)

Config keys: `influxdb.timeout` (ms, default 10000), `influxdb.slow_query_timeout` (ms, default 60000).

**`write_forecast_grid` — chunked writes**: Grid data (~1.17M points per run) is written in chunks of 5000 pts per InfluxDB call. A single bulk write caused read-timeout failures (~8.75 s) against the default 10s timeout.

### ⚠️ `contains(value:, set:)` is catastrophically slow against high-cardinality measurements (v1.22.6)

Filtering multiple station ids with Flux's `contains(value: r.station_id, set: [...])` measured
**9.3 s** for a single-station, ±30 min query against `weather_forecast` — the *same* query with
an OR-chain of `r.station_id == "..."` took **69 ms**. 135× difference, confirmed by direct A/B
timing against InfluxDB, not inferred. Root cause: `weather_forecast`'s per-hour `init_date` tag
fragments it into a huge number of series over time (infinite retention, running since v1.15),
and `contains()` cannot use the tag index to skip non-matching series the way `==` can.

**Rule going forward: never use `contains(value:, set:)` against `weather_forecast` or
`weather_forecast_thermal`.** Always build an OR-chain string (`" or ".join(f'r.station_id ==
"{sid}"' for sid in ids)`), exactly as `query_forecast_for_stations` already did before this was
even discovered as a bug elsewhere. `contains()` against `weather_data` measured fast (147 ms) —
lower cardinality, no per-hour tag fragmentation — and was left alone; the five `contains()`
call sites still in `influx.py` all target `weather_data` only. Re-check this if that changes.

---

### ⚠️ Forecast model-run selection is shared by every reader (v1.23.1 / v1.23.2)

`init_date` is a **tag**, so every model run is its own series and `weather_forecast` retains many
runs for the same `valid_time`. Two rules, applied identically by all three readers — that shared
rule is what guarantees the map's wind arrows and the rule set's traffic light never disagree:

1. **Same candidate runs** — `_forecast_init_date_filter()` builds the Flux `init_date` predicate
   from `_recent_forecast_init_dates(depth=FORECAST_RUN_FALLBACK_DEPTH)`.
2. **Per-field, newest-run-that-has-it** — `_merge_forecast_candidates()` orders candidates by
   `(preferred source, newest init_date, tiebreak)` and takes each field from the first candidate
   with a non-`None` value. A newer run supplies everything it has; an older run fills only the
   gaps it left, never the reverse.

| Reader | Used by |
|---|---|
| `query_forecast_replay` | map wind arrows in replay |
| `query_forecast_snapshot_for_stations` | `run_forecast_evaluation_at` (replay decision fallback), `GET /api/foehn/forecast` |
| `query_forecast_for_stations` | `run_forecast_evaluation` (the precomputed horizon), station forecast charts |

Each strips the provenance keys its response shape never carried (`init_date` for the horizon
reader; `source`/`model`/`init_date` for replay), so shapes are unchanged.

**Why fall back at all:** lsmfapi legitimately serves null frames at the ICON-CH1/CH2 seam — every
run's hole ends at its *own* h+33, with CH2 data resuming at h+34. Consecutive runs therefore have
overlapping but offset holes, and reading only the newest run blanks those hours entirely. Measured
against prod recovering a 15-hour hole: depth 1 → 0/15, depth 2 → 6/15, **depth 3 → 15/15**. Cost
~+1.4 s per extra run on the replay query, so 3 is where coverage saturates.

**`FORECAST_RUN_FALLBACK_DEPTH = 3` is a workaround, not a permanent constant.** The seam has been
reported to the lsmfapi owner (`features.md` backlog: "Upstream: lsmfapi CH1/CH2 stitch nulls
h+19–h+33"). If fixed upstream, drop this back to `1` and replay latency falls from ~4.4 s to
~1.5 s. Don't lower it speculatively — re-measure against prod first, the same way it was
determined here.

**Never resolve a run with `|> last()`.** It returns the last point *per series*, i.e. one record per
`init_date`, not the newest run. Paired with a "keep the entry with the most fields" tiebreak this
resolved to the **oldest** retained run and shipped a four-day-old forecast to the rules engine
while the arrows showed the current one (fixed v1.23.1).

**Never write a point whose every weather field is null.** `write_forecast` /
`write_thermal_forecast` write `init_time` unconditionally, so an all-null frame used to emit a
point holding *only* `init_time` — and that phantom row, belonging to a newer run, shadowed a
complete row from the previous run in every newest-wins dedup, turning real data into `None`. Both
writers now skip the point (fixed v1.23.2). The read-side merge above tolerates phantom rows
already stored, so the fix is retroactive.

## API Contracts

### Stations
- `GET /api/stations` — list all active stations (`?network=&canton=`)
- `GET /api/stations/{station_id}` — station metadata
- `GET /api/stations/{station_id}/latest` — most recent measurement
- `GET /api/stations/{station_id}/history` — `?from=&to=&fields=`
- `GET /api/stations/replay` — `?start=&end=&forecast_hours=&include_forecast=` — all stations over a time window for map replay; **server-side in-memory cache (5 min TTL)** keyed by query params; Flux query uses `aggregateWindow(30m, last)` before pivot; forecast data merged via two-step `query_forecast_replay`
- `GET /api/stations/{station_id}/forecast` — `?hours=N` — forecast points; response includes `forecast_source` and `forecast_model` from first data row
- `GET /api/stations/{id}/forecast-accuracy` — `?from=&to=`

### Wind Forecast Grid
- `GET /api/wind-forecast/grid?date=YYYY-MM-DD&level_m=1500` — requires `require_pilot`; maps `level_m` → `level_hpa` via `ALTITUDE_TO_HPA`; returns `{date, level_m, level_hpa, grid:[{lat,lon},...], frames:[{t, ws:[...], wd:[...], rh:[...]},...]}`; **grid is built dynamically from InfluxDB data** (not hardcoded 171-point assumption); `grid_id` tag used as canonical key (avoids float-precision issues); grid sorted lat desc, lon asc; arrows show cloud icon when `rh >= 90`

### Public (unauthenticated)

**Policy: any public-worthy data is open.** Weather-station routes (`/api/stations*`) carry no auth
dependency, and the föhn read routes (`/status`, `/forecast`, `/observation`, `/history`) use
`get_current_user_optional` — both are open **by design** (owner decision 2026-10-06; this is the
basis for the public MCP server, `specs/010-mcp-server`). Only pilot-owned data (rule sets, decisions,
föhn *config*, accounts) is gated. `/api/public` below is specifically the open surface for **rule sets**.

- `GET /api/public/rulesets/map` — **the only unauthenticated route in the rule set surface.**
  Curated examples for visitors: `is_showcase AND is_public`, positioned, evaluated against real
  data. Returns `{data: [{id, name, lat, lon, site_type, decision}], generated_at}` — a narrow
  payload that deliberately does **not** reuse `RuleSetOut` (which carries `owner_display_name` and
  would leak owner identity by default). Served from a shared 60 s cache in
  `services/public_map.py`, so cost does not scale with visitors.
- `GET /api/rulesets/public-map` — signed-in equivalent: other owners' published rule sets, minus
  any within **500 m** of one of the viewer's own (`haversine_m` from `services/dedup.py`).
  Per-viewer, therefore **never** served from the anonymous cache entry.
- `PUT /api/rulesets/{id}/set_showcase` — admin curation toggle. **409** if the owner has not
  published it; un-curating is always allowed.

**Rule sets resting on missing data are omitted, not shown.** For an *exception-only* rule set,
`run_evaluation` still returns green when nothing triggers, including on no data ("unknown = benefit
of the doubt") — fine for a pilot who can see `no_data_stations`, a lie to a visitor. (A rule set with
a GREEN requirement instead evaluates *red* on no data since v1.20.0 — but that is also not something
to assert to an anonymous visitor about a site whose stations are down.) Either way the public builder
**drops any rule set with a missing station** before evaluation, so neither false-green nor
questionable-red reaches the anonymous map. Note the frontend's `if (!dec) return;` guard catches only
*failed requests*, not this.

### Remaining routers

Not enumerated here — read the router file, which is the source of truth. (`07-api-conventions.md`
defines the response/error *format*, not the route list; it is not a route reference.)

| Prefix | File | Routes |
|---|---|---|
| `/api/auth` | `routers/auth.py` | 10 |
| `/api/rulesets` | `routers/rulesets.py` | 15 — rulesets carry site identity, gallery, presets, webcams, landing links |
| `/api/admin` | `routers/admin.py` | 10 |
| `/api/foehn` | `routers/foehn.py` | 7 |
| `/api/stats` | `routers/stats.py` | 6 |
| `/api/org` | `routers/org.py` | 3 — `/{slug}/status\|dashboard\|rulesets` |
| `/api/ai` | `routers/ai.py` | 1 |
| `/api/health` | `routers/health.py` | 1 |
| — | `routers/pages.py` | 20 HTML page routes (no `/api` prefix) |

There is **no launch-sites API** — a "launch site" is a `ruleset` with `site_type="launch"`.

---

## Public MCP Server (v1.24.0, `specs/010-mcp-server`)

`POST /mcp` — Model Context Protocol over **stateless Streamable HTTP with JSON responses**, no auth,
read-only. Code in `src/lenticularis/mcp_server/`; config block `mcp:` (`McpConfig`). Six tools:
`search_stations`, `get_current_weather`, `get_weather_history`, `get_forecast`, `get_foehn_status`,
`describe_service` (+ resource `lenticularis://about`). Contracts: `specs/010-mcp-server/contracts/mcp-tools.md`.
Status/usage: `GET /api/health/mcp`.

**Verified stations only.** `mcp.verified_networks` is a fail-closed **allowlist** (default
meteoswiss, slf, metar, holfuy, windline, fga, jfb). Wunderground/Ecowitt are private stations and are
never served. A newly added collector stays hidden until listed (see `prompts/add-collector.md` step 3b).

⚠️ **MCP has its own registry — never reuse the website's `display_registry`/`virtual_members`.**
The website dedup merges co-located stations across ALL networks and `query_latest_virtual` /
`query_history_virtual` pool every member newest-wins, so a MeteoSwiss station with an Ecowitt neighbour
would leak Ecowitt values. `McpRegistry` (`mcp_server/registry.py`) drops private networks **before**
dedup, then dedups on its own. It is rebuilt inside `rebuild_display_registry` and the registry updater in
`main.py`. Consequence: `jfb` ranks *below* ecowitt/wunderground in `NETWORK_PRIORITY`, so a cluster whose
website canonical is a private station has a different canonical id (and values) in MCP — by design.

Other load-bearing details (all learned in the Phase 0 spike):
- **No `Mount`.** `Mount("/mcp")` answers `POST /mcp` with a 307 to `http://…/mcp/` (uvicorn does not trust
  `X-Forwarded-Proto` behind Traefik). `create_app()` registers explicit `Route("/mcp")` + `Route("/mcp/")`
  that forward to the sub-app at path `/` (`McpEndpoint` → `McpHandle.__call__`).
- **The handle is built in the lifespan** (config is available there; `app = create_app()` runs at import).
  `session_manager.run()` is entered once, in the lifespan. Until then / if disabled → clean 503.
- **Constructing `FastMCP` reconfigures the root logger** — `McpHandle._build` snapshots and restores it.
- DNS-rebinding protection is on: `mcp.allowed_hosts` must list every public hostname (`421` otherwise).
- Pin `mcp>=1.30,<2` — mcp 2.x renames `FastMCP`→`MCPServer` and pulls different HTTP deps.
- Rate limit is applied **inside the tool layer** (LLM-visible `RATE_LIMITED … Retry in N seconds`),
  keyed by the rightmost `X-Forwarded-For` entry (`trusted_proxy_hops`, default 1) hashed with a random
  per-process salt. Unexpected exceptions are logged and returned as generic `INTERNAL_ERROR`.
- History (`InfluxClient.query_history_range`) aggregates **per field**: gust=max, precipitation=sum,
  direction/snow_depth=last, rest=mean — a blanket mean would understate peak gusts.
- Föhn tools use the **system default config only**, and drop any input station whose network is not
  verified at runtime (the default config is admin-editable live).
- Forecast reports `forecast_issued` via `query_forecast_for_stations(keep_init_date=True)`; missing hours
  are listed, never interpolated. Known-faulty values (Hollandiahütte temp/humidity/pressure) are removed in
  one choke point, `mcp_server/sanitize.py:SUPPRESSED_FIELDS`.
- Influx readers return `{}` on failure, so a database outage reads as "no data" — documented, not fixed.

Out of scope / backlog: pilot rule sets and decisions (needs auth), thermal and wind-aloft tools (unverified).

---

## Rules Engine Design

`rules/evaluator.py`:
1. Load the flat condition list from SQLite (not a tree — see below)
2. Fetch latest measurements from InfluxDB for **all** stations in one batch (`query_latest_for_stations`)
3. Apply operator/value logic → per-condition colour
4. Bucket conditions by `group_id`: same non-NULL `group_id` → ANDed; `group_id = NULL` → standalone
5. Apply the requirement rule (below), then `combination_logic` (`worst_wins` or `majority_vote`)
6. Return `TrafficLightDecision` + write to `rule_decisions` InfluxDB

**Grouping is one level deep — AND only.** There is no OR-group and no nesting. Do not document or
build against a "condition tree".

### GREEN conditions are requirements (v1.20.0, `specs/archive/004`) — refined v1.22.4

For **launch/landing** sites, a GREEN unit (a standalone GREEN condition, or an AND group whose
effective colour `_worst(members)` is green) is a **requirement**. When it does **not** trigger,
it flags `unmet_green = True` rather than appending `"red"` immediately — the actual `"red"`
fail-safe only fires **after both loops complete, and only if nothing else triggered at all**:

```python
unmet_green = False
for cond in standalone:
    if matched:
        triggered_colours.append(cond.result_colour)
    elif ruleset.site_type != "opportunity" and cond.result_colour == "green":
        unmet_green = True
# ... same pattern in the group loop ...
if unmet_green and not triggered_colours:
    triggered_colours.append("red")
```

⚠️ **This was unconditional before v1.22.4** — the fail-safe used to append `"red"` the instant a
green unit missed, *regardless of whether a different group in the same ruleset had already
matched*. Real-world bug this caused: a launch ruleset with a green direction arc (e.g. 90-180°)
plus separate orange arcs for other directions (a common, legitimate pattern, not an "exception")
always read red whenever the wind came from any of the orange arcs — the orange arc's own match
was silently overridden by the unrelated unmet green arc. Fixed by deferring the fail-safe: it
still forces red for the original spec-004 case (a lone green requirement, nothing else defined to
classify the miss), but a genuinely matched other group now stands.

- **"Not triggered" folds no-data into threshold-failure.** `_eval_condition` returns `(False, …)`
  for both a missing station and a failed comparison, so an unconfirmable GREEN requirement **fails
  safe to red** when nothing else classifies the conditions either (spec 004 D3).
- **RED/ORANGE keep exception semantics.** They contribute only when matched; otherwise silent. A
  rule set built only from exception conditions therefore still defaults to `"green"` when nothing
  triggers — **the benefit-of-the-doubt default survives for exception-only sets, and only there.**
- **Opportunity is gated out** (`!= "opportunity"`). It already forces red when
  `len(triggered_colours) < total_units`; appending red would double-count and could flip that guard.
- **Mixed groups stay exception-style** (D2): only a unit whose effective colour is green is a
  requirement, mirroring how `worst_wins` collapses a group to one colour.
- The rule is duplicated across four decision blocks (`run_evaluation`, `run_evaluation_at`,
  `run_forecast_evaluation`, plus `_evaluate_from_station_data` itself) — a flagged follow-up is to
  route them through the shared core. `run_forecast_evaluation_at` (added specs/archive/007, below) does
  **not** add a fifth copy — it calls `_evaluate_from_station_data` directly. The v1.22.4 fix was
  applied identically to all four copies; see `tests/backend/test_unmet_green_precedence.py`.

**The evaluator buckets groups from the conditions — never from `condition_groups` rows.** This is
load-bearing and must not be "cleaned up":

- An empty group contributes no conditions, so it never becomes a bucket, never counts toward
  `total_units`, and is inert **by construction** rather than by a guard someone must remember.
- Iterating group rows instead would hit `all([]) → True` (vacuous) and then `_worst([])`, which is
  `max()` of an empty sequence → **`ValueError`**, killing evaluation for the whole rule set.
- `group_name` on `ConditionResult` is populated by a lookup layered onto the *output* only
  (`_group_names()`); it can never influence a decision.

A one-condition group evaluates identically to a standalone condition: `total_units` is
`len(standalone) + len(groups)`, so which bucket a lone condition lands in does not change the count,
and a one-member group contributes `_worst([c])` — that same colour.

### Evaluation is event-driven, not scheduled (v1.23.0, `specs/009`)

There is **no** fixed-interval ruleset evaluation job — the `IntervalTrigger(minutes=10)` and its
`collector_ruleset_evaluator` job id were removed. Evaluation is triggered by the existing
`on_collector_run` / `on_forecast_run` scheduler hooks:

- `scheduler.evaluate_rulesets(station_ids, trigger)` — live decisions, after an observation run.
- `scheduler.evaluate_rulesets_forecast(station_ids, horizon_hours, trigger)` — the whole forecast
  horizon, after a forecast run.
- Both resolve *which* rule sets to touch via `rules/reactive.py`'s `affected_ruleset_ids()`, a
  single `SELECT DISTINCT` over `rule_conditions` (matching `station_id` **or** `station_b_id`),
  never a per-station loop.
- Station ids are expanded across the whole dedup cluster **in both directions** (reported→canonical
  *and* canonical→all members). One-way expansion is not sufficient: canonicality is priority-ranked,
  so a new higher-priority station near an existing one moves the canonical id and strands older
  conditions on what is now a member.
- `try_claim`/`release` (module-level set + lock, self-draining) collapse a burst of overlapping
  collector runs to one evaluation per rule set. A refused claim is **skipped, not queued**.
- One full pass runs at boot from `main.py`'s lifespan, so nothing is indefinitely stale after a
  restart.

⚠️ `scheduler.on_collector_run` / `on_forecast_run` are **single callable slots, not listener
lists**. A second assignment silently discards the first — `main.py` composes everything into one
callback via `_compose_collector_hook` / `_compose_forecast_hook`.

Health is still reported under the `ruleset_evaluator` key (`interval_minutes: None`,
`status: "reactive"`, plus `last_affected_count`), so `/stats` keeps a visible row.

Public entry points: `run_evaluation`, `run_evaluation_at`, `run_forecast_evaluation`,
`run_forecast_evaluation_at`, `run_history_backfill`, `write_decisions_batch`. The core is
`_evaluate_from_station_data(ruleset, station_data) -> (decision, results)`. There is **no**
`evaluate_ruleset()` function in `evaluator.py` (the router's endpoint handler of that name in
`rulesets.py` is routing glue, not an evaluator entry point).

Forecast evaluation reuses identical logic over hourly `valid_time` steps. Does NOT write to InfluxDB.

**`run_forecast_evaluation_at(ruleset, influx, valid_time)`** (specs/archive/007) — single-`valid_time`
forecast lookup, the forecast-side counterpart to `run_evaluation_at`. Built on
`InfluxClient.query_forecast_snapshot_for_stations` (±30 min window in `weather_forecast`, already
used by `GET /api/foehn/forecast`) rather than a new query. `GET /api/rulesets/{id}/evaluate` picks
between `run_evaluation_at` (default) and this function via a new `forecast: bool = False` query
param — the caller (the map's replay engine) states the mode explicitly rather than the server
inferring it from comparing `at_time` to its own clock. Linked landing rulesets (the launch-site
halo) are now evaluated in the same `at_time`/`forecast` mode as the primary rule set — previously
always live regardless of the primary rule set's mode.

**Since v1.23.0 the `forecast=true` branch reads first, evaluates second.** `_evaluate_at` tries
`query_forecast_decisions_for_ruleset` for the nearest stored hour within ±30 min of `at_time`, and
only calls `run_forecast_evaluation_at` when nothing is stored. ⚠️ **Do not remove that fallback** —
a rule set created between two forecast collector runs, or one whose horizon write failed, has
nothing precomputed and must still resolve on demand. `no_data_stations` is *derived* on the cached
path (a station whose every condition came back with `actual_value is None`), since only `decision`
and `condition_results` are persisted.

---

## Replay Cache Architecture

`api/routers/stations.py` module-level `_replay_cache: dict[str, tuple[Any, float]]` (key → payload, monotonic stored_at). TTL 5 min.

**Warm-up**: background `asyncio.Task` at startup iterating offsets `[1, 0, 2, -1, 3, -2, 4, -3, 5]` sequentially.

**Cache poisoning guard**: skip writing when `fc_frame_count == 0` and `include_forecast` is true — prevents obs-only entries from blocking forecast data.

**Post-forecast invalidation**: `main.py` lifespan wires a real async hook via `scheduler.on_forecast_run = _make_forecast_hook(influx, display_registry)`. After each successful forecast run (`status == "ok"` and `measurement_count > 0`), the hook calls `invalidate_forecast_replay_cache()` then spawns `warm_replay_cache()` as a background task.

### ⚠️ Stale-while-revalidate on read (v1.23.3)

The TTL (5 min) is shorter than the only two events that repopulate the cache — startup and
the hourly forecast collector run. That left a **~55-minute gap per hour** where the entry was
technically expired: the *previous* behaviour deleted a stale entry on read and rebuilt
synchronously (the reported ~10s cold-miss), so whichever request happened to land in that gap
paid the full `_build_replay_payload` cost (InfluxDB obs + forecast query, ~1.5–6s depending on
`FORECAST_RUN_FALLBACK_DEPTH`), and the *next* request within 5 min looked fast purely because
someone else had already eaten that cost.

`GET /api/stations/replay` now serves a TTL-expired entry **immediately** and refreshes it via
`_schedule_replay_refresh()` as a background `asyncio.Task` — no request ever blocks on a
rebuild. `_replay_refresh_inflight: set[str]` (guarded by a `threading.Lock`, mirroring the
`_TTLCache` pattern) ensures at most one refresh per cache key runs at a time; a second stale
hit while one is in flight is a no-op, not a second rebuild. The refresh honours the same
cache-poisoning guard as every other write path — an empty-forecast rebuild is discarded, not
stored, so the stale-but-populated entry keeps being served (and retried) rather than being
replaced with a hole.

A **true** cache miss (no entry at all — a custom date outside the 9 pre-warmed offsets, or a
cold cache after restart) still builds synchronously; only an *expired* entry gets the
stale-serve treatment, since there is nothing to serve instead on a genuine miss.

`tests/backend/test_replay_cache.py` — fresh hit skips rebuild entirely; stale hit serves the
stale payload and the background refresh lands in the cache afterward; two concurrent stale
hits (`asyncio.gather`, not sequential — sequential awaits let the first request's refresh
finish before the second starts) trigger exactly one rebuild; true miss builds synchronously
and stores; an empty-forecast refresh result is discarded and the in-flight guard still clears.

**Both `GET /api/stations` and `GET /api/stations/replay` still return every station in one atomic
payload** — no bounding-box/`station_ids` query parameter exists on either (specs/archive/008, deliberately
rejected: it would fragment this shared cache into one entry per viewport per pilot instead of one
entry per day shared by everyone, see below).

---

## Viewport-First Station Rendering (`static/map.js`, specs/archive/008)

**Client-side only — no API/cache change.** Since both station endpoints already return every
station in one payload, "viewport-first" is a **render-order** concept, not a fetch-order one:
`_renderStationsViewportFirst()` places stations inside `map.getBounds()` synchronously, defers the
rest via chunked `requestIdleCallback` (`_deferChunked`, `_pendingOffscreen`), and a `moveend`
listener promotes newly-visible stations out of that pending queue on pan/zoom without touching or
duplicating already-placed markers. Applied at every station-marker render site: `loadStations()`,
`applyReplaySnapshot()`, and (transitively) the 60 s live refresh. **Ruleset markers
(`loadRulesetMarkers`, specs/archive/007) are untouched** — out of this feature's scope.

A bounding-box query parameter on `/api/stations/replay` was considered and rejected: `_replay_cache`
is shared across every pilot viewing the same day-offset, which is what makes `warm_replay_cache`'s
startup warm-up valuable (one InfluxDB query serves everyone). Scoping by viewport would fragment
that into one cache entry per pilot's individual pan position instead.

**Geolocation centering**: `map.js` attempts `navigator.geolocation.getCurrentPosition()` once,
asynchronously, right after the map is already painted at its Interlaken/zoom-11 default — never
blocking first paint. `localStorage['lenti_geo_pref']` (`"granted"`/`"declined"`) remembers only an
explicit `PERMISSION_DENIED`; a timeout or unavailable position writes nothing, so a prior grant
retries on the next visit rather than being permanently revoked by a transient failure. An explicit
"center on me" Leaflet control (mirrors the existing `_PersonalToggle` pattern) can always retry
regardless of the stored preference. No location data is ever sent to the backend.

---

## Föhn Detection Design

`foehn_detection.py` — shared between scheduler and API router.

**Delta/trend conditions** (`lookback_h` set): evaluates `current − historical[lookback_h][station][field]` vs threshold. Supports `humidity Δ2h < −10` (föhn arriving) patterns.

**Virtual foehn stations**: `foehn-beo`, `foehn-haslital`, `foehn-wallis`, `foehn-reussthal`, `foehn-rheintal`, `foehn-guggi`, `foehn-overall`. Written to `weather_data` every 10 min by `FoehnCollector`. Field: `foehn_active` (1.0/0.5/0.0/−1.0).

**Config**: `data/foehn_config.json` system-wide default; `user_foehn_configs` SQLite for per-user overrides.

---

## Virtual Station Deduplication

`services/dedup.py` — `build_deduped_registry(raw, distance_m=50.0, manual_pairs=None)`

1. Exclude `foehn` network
2. Union-find over pairs within 50 m (Haversine)
3. Union manual pairs from `station_dedup_overrides`
4. Pick canonical by priority: meteoswiss > slf > metar > holfuy > windline > ecowitt > wunderground > jfb
5. Non-canonical stations omitted from `display_registry`

**History**: member must have data in 2 h slice BEFORE window to be included.

---

## Static Asset Delivery

`api/routers/pages.py` + the security-headers middleware in `api/main.py`.

| Concern | Behaviour |
|---|---|
| Libraries | Leaflet 1.9.4 + Chart.js v4 self-hosted in `static/vendor/`. **No CDN** — CSP is `script-src 'self'` / `style-src 'self'`, so external refs are browser-blocked. |
| Cache-busting | `_page()` rewrites every local `href`/`src="/static/…"` to append `?v=<app-version>` at serve time. `_APP_VERSION` comes from `importlib.metadata.version("lenticularis")` → `pyproject.toml`. |
| Versioned assets | `?v=` present → `Cache-Control: public, max-age=31536000, immutable` |
| Unversioned assets | No `?v=` (locale JSON, ES-module imports) → `Cache-Control: public, max-age=600` |
| HTML | `Cache-Control: no-cache` + `ETag: "<version>-<mtime>"`; revalidates to `304`. Re-read per request so dev volume-mount edits stay live. |

**A version bump in `pyproject.toml` is mandatory when static assets change** — the version is
the cache key. Without it, changed assets stay pinned in browsers for a year.

---

## Deployment

### Versioning & Image Publishing

Version is single-sourced in `pyproject.toml`, read at runtime via `importlib.metadata`.

Pushing a `v*` git tag triggers `.github/workflows/docker-publish.yml`, which builds multi-arch
(amd64 + arm64) and publishes to `ghcr.io`. `docker/metadata-action` derives all tags from the
one git tag — `v1.18.1` produces `:v1.18.1`, `:1.18.1`, `:1.18`, `:1`, **and `:latest`**. There
is no separate "latest" tag to push.

Tags must be 3-part semver (`v1.2.3`) or the semver patterns do not activate.

### Traefik Labels — list format only
```yaml
labels:
  - "traefik.enable=true"
  - "traefik.http.routers.myapp.rule=Host(`myapp.lg4.ch`)"
```

### Healthcheck
```yaml
healthcheck:
  test: ["CMD-SHELL", "python -c \"import urllib.request; urllib.request.urlopen('http://localhost:8000/')\""]
```

### Dev Overlay
`docker-compose.dev.yml` extends base with live `src/` and `static/` volume mounts, Traefik labels for `lenti-dev.lg4.ch`, `PYTHONPYCACHEPREFIX=/tmp/pycache`.

### SSH / Deploy
SSH hosts `xpsex` and `sdh` are for **read-only investigation only** (`docker logs`, `docker ps`,
`docker exec ... <read-only command>`, curl). The user syncs files, pulls new images, and restarts
containers manually. Never rsync, push files, or restart/recreate containers on either host.

`sdh` (confirmed 2026-08-01) is a shared multi-service Docker host — the `lenticularis` container runs
alongside many unrelated services. It serves `lenti.cloud` and `lenti.sdh.lol` via Traefik; the
SQLite file inside the container is at `/app/data/lenticularis.db`; the InfluxDB container is named
`lenticularis-influxdb`.
