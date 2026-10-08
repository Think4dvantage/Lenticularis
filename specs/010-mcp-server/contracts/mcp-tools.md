# MCP Tool Contracts — Release 1 (public, read-only)

Endpoint: `POST /mcp` (Streamable HTTP, stateless, JSON responses). No auth. Every tool is annotated
`readOnlyHint: true`, `openWorldHint: false`. Errors are MCP tool errors (`isError: true`) with text of the
form `<code>: <what happened>. <what to try>` — codes follow the project vocabulary
(`ENTITY_NOT_FOUND`, `VALIDATION_FAILED`, `RATE_LIMITED`, `INTERNAL_ERROR`). Times are ISO-8601 UTC.

Descriptions are written for an LLM reader: they say *when* to use the tool and which tool to call first.

## `search_stations`
Find weather stations by name or place. **Call this first** — station ids are not guessable.
| Arg | Type | Notes |
|---|---|---|
| `query` | string? | case-insensitive substring of name |
| `lat`, `lon` | number? | both or neither; sorts nearest-first |
| `radius_km` | number? | default 25 with lat/lon, max 200 |
| `network` | enum? | one of verified networks |
| `canton` | string? | 2-letter |
| `limit` | int | default 10, max `max_search_results` |
→ `{count, truncated, stations: [Station]}`. Zero matches → success with `count: 0` and a `hint`.

## `get_current_weather`
Latest measurements for one station. Use `search_stations` to get the id.
| Arg | Type |
|---|---|
| `station_id` | string |
→ Observation. If nothing in 24 h: `values: {}`, `stale: true`, message "no recent data".

## `get_weather_history`
Measured values for a past period. Either `hours` (lookback) or `start`+`end`.
| Arg | Type | Notes |
|---|---|---|
| `station_id` | string | |
| `hours` | int? | 1–720 |
| `start`, `end` | datetime? | ISO-8601; mutually exclusive with `hours` |
| `fields` | string[]? | default: all available |
| `resolution` | enum? | `auto`(default) `10m` `30m` `1h` `3h` `1d` |
→ Historical series. `downsampled`/`resolution` always stated; empty range → success, `count: 0`, `hint`.

## `get_forecast`
Hourly forecast for one station (ICON-CH ensemble median, `*_min`/`*_max` spread when present).
| Arg | Type |
|---|---|
| `station_id` | string |
| `hours` | int, default 48, max 120 |
→ Forecast. `missing_hours` lists ranges with no usable data; note explains the known model-seam gap.

## `get_foehn_status`
Föhn state per Swiss region (active / partial / inactive / no_data) plus pressure-gradient indicators, using the
system default definition (never a pilot's config).
| Arg | Type | Notes |
|---|---|---|
| `at` | datetime? | omitted = live; past = observed snapshot; future (≤120 h) = forecast |
→ Föhn status payload.

## `describe_service` (P2, US6)
No args. → Service description. Also exposed as MCP resource `lenticularis://about`.

## Out of Release 1 (backlog)
`get_site_conditions`/ruleset tools (needs auth), thermal & wind-aloft tools (unverified), area summary (P3).
