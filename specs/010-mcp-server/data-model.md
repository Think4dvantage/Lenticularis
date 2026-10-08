# Data Model: MCP Server (spec 010)

**No SQLite or InfluxDB schema change.** Phase 1 adds one InfluxDB *read* method, config keys, and in-memory
state only. (No Alembic concern; no new measurement.)

## Config (`config.py` + `config.yml.example`)

```yaml
mcp:
  enabled: true
  path: /mcp
  verified_networks: [meteoswiss, slf, metar, holfuy, windline, fga, jfb]   # allowlist, fail-closed
  rate_limit_per_minute: 60
  rate_limit_max_callers: 1000
  trusted_proxy_hops: 1
  max_history_points: 500
  max_search_results: 25
  stale_after_minutes: 120
```
`McpConfig(BaseModel)` with those defaults; `MainConfig.mcp: McpConfig = McpConfig()`. Never `os.environ`.

## In-memory state

| Name | Shape | Owner | Bound |
|---|---|---|---|
| `mcp_registry` | `dict[station_id, Station]` — verified stations, deduped | `mcp/registry.py` | #stations (~hundreds) |
| `mcp_virtual_members` | `dict[canonical_id, list[member_id]]` — verified members only | same | same |
| rate-limit table | `dict[caller_key, deque[float]]` + `threading.Lock` | `mcp/ratelimit.py` | `rate_limit_max_callers`, oldest-first eviction |
| usage counters | `dict[tool, {calls, errors, total_ms}]` + distinct-caller HyperLog-lite (set capped) | `mcp/usage.py` | fixed key set; caller set capped |

## Entities → tool payloads (see `contracts/mcp-tools.md`)

| Spec entity | Payload | Source | Notes |
|---|---|---|---|
| Station | `{station_id, name, network, canton, latitude, longitude, elevation_m, distance_km?}` | `mcp_registry` | no `latest`; no foehn network |
| Observation | `{station_id, time, age_minutes, stale, values{field:float}, units, source}` | `query_latest[_virtual]` over verified members | null fields omitted; suppressed fields removed |
| Historical series | `{station_id, start, end, resolution, downsampled, count, units, data[{time, …}]}` | new `query_history_range` | ≤ `max_history_points` |
| Forecast | `{station_id, source, model, forecast_issued, units, timezone:"UTC", data[{time,…}], missing_hours, note}` | `query_forecast_for_stations(keep_init_date=True)` | shared run-merge rule unchanged |
| Föhn status | `{assessed_at, is_forecast, valid_time?, regions[…], pressures[…]}` | `foehn_detection.build_response` with default config | unchanged shape of existing builder |
| Service description | `{name, version, coverage{networks, station_count}, units, update_cadence, limitations[], data_policy}` | static + registry | US6 |

## Validation rules
- `station_id`: `^[\w\-]{1,64}$` **and** present in `mcp_registry` (or a verified member id) → else `not_found` error.
- `hours`: 1–720 history lookback, 1–120 forecast. `start < end`, range ≤ 365 days, `end ≤ now`.
- `lat ∈ [-90,90]`, `lon ∈ [-180,180]`, `radius_km ∈ (0, 200]`.
- Field list ⊆ `{wind_speed, wind_gust, wind_direction, temperature, humidity, pressure_qfe, pressure_qff, precipitation, snow_depth}`.

## Invariants (each has a test)
1. No station with `network ∉ verified_networks` is reachable by any tool, directly or as a dedup member.
2. No field in `SUPPRESSED_FIELDS[station_id]` appears in any payload.
3. No pilot-owned entity (ruleset, user, decision) type is imported by `mcp/` (import-linter-style grep test).
4. Every payload carries `units`, `source`, and UTC timestamps.
