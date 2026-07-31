# Data Model: 2.x → v3 Mapping

No SQLite change. This migration touches only the time-series store. The relational model
(`database/models.py`, the ten SQLite tables) is untouched.

## Concept mapping

| InfluxDB 2.x | InfluxDB 3 | Notes |
|---|---|---|
| Organisation (`org`) | — | Concept removed in v3. `InfluxDBConfig.org` dropped. |
| Bucket (`bucket`) | **Database** | `InfluxDBConfig.bucket` → `database`. One database for Lenti. |
| Measurement | **Table** | The four below map 1:1. |
| Tag | **Tag column** (series key) | Same tag names, still indexed/series-defining. |
| Field | **Field column** | Same field names/types (all floats + a few strings). |
| `_time` | `time` | Primary timestamp column. |
| Retention policy | Database retention period | Set per database; preserve current retention. |

## Measurements (carried over unchanged — source: `architecture.md`)

### `weather_data`
- **Tags**: `station_id`, `network`, `canton`
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qfe`,
  `pressure_qff`, `precipitation`, `snow_depth`; virtual föhn stations also `foehn_active`
  (1.0 / 0.5 / 0.0 / −1.0).

### `weather_forecast`
- **Tags**: `station_id`, `network`, `model`, `source` (`swissmeteo`/`open-meteo`), `init_date`
  (`YYYY-MM-DDTHH`)
- **Timestamp**: `valid_time` (the future moment forecast is valid for) → stored as `time`.
- **Fields**: `wind_speed`, `wind_gust`, `wind_direction`, `temperature`, `humidity`, `pressure_qff`,
  `precipitation`; swissmeteo also `_min`/`_max` variants; `init_time` (ISO string, Python-side dedup).

### `wind_forecast_grid`
- **Tags**: `grid_id` (`"47.9000_5.9000"` swissmeteo 4-dp / `"46.00_7.00"` open-meteo 2-dp),
  `level_hpa`, `init_date`
- **Timestamp**: `valid_time` (UTC) → `time`.
- **Fields**: `wind_speed` (km/h), `wind_direction` (deg int), `humidity` (%), `lat`, `lon`.
- **Volume**: ~1.17 M points/run — dominates the data-migration export/load.

### `rule_decisions`
- **Tags**: `ruleset_id`, `owner_id`, `site_type`
- **Fields**: `decision` (green/orange/red — string field), `condition_results` (JSON-array string field).

## Migration parity checks (per measurement)

For each of the four measurements, the D3 export/load is accepted only when:

- Row count on v3 == row count on 2.7 (per measurement; grid checked per `init_date` chunk).
- `MIN(time)` and `MAX(time)` match (data-bounds parity).
- A spot-sampled set of series (by tag key) returns identical field values at identical timestamps.

## Invariants that must hold on v3 (from `04-constraints.md` T19)

- **No two fields with the same key in one point/row** (Flux silently dropped one; ensure the v3 write
  path never emits a duplicate field column).
- **`_source`/dedup guards compare values, not presence** — carry the existing guard semantics; do not
  regress to a truthy-presence check.
- **String vs float field typing** must match 2.x exactly (`decision`/`condition_results`/`init_time`
  are strings; everything else float) — v3 is stricter about column types across writes to the same
  table, so a first write establishes the type. Establish types deliberately, matching 2.x.
