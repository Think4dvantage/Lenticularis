# Research: InfluxDB 2.7 → 3 Migration

Phase 0 findings. Resolves the technical unknowns behind `spec.md`. Items marked **VERIFY** must be
confirmed against the live v3 docs / the actual v3 instance during Phase 1 of implementation — they are
things I will not assert from memory.

---

## R1 — Why v3 at all (Flux end-of-life)

- **Decision**: Migrate to InfluxDB 3; do not stay on 2.x long-term.
- **Rationale**: InfluxData's active development is the 3.x line. **InfluxDB 3 does not support Flux at
  all** — it queries via SQL (Apache DataFusion) and InfluxQL. Flux itself is in maintenance mode. The
  entire `influx.py` is Flux (32 `from(bucket:)`), so "keep getting updates" and "keep Flux" are
  mutually exclusive.
- **Alternatives**: Stay on 2.7 (supported today, terminal line) — kept as D1 option (d).

## R2 — Edition: Core vs Enterprise (the gate)

- **Decision**: Deferred to user (D1). Technically, **Enterprise is required for this workload**; Core
  is not viable.
- **Rationale** (sources below): Core defaults to a **72-hour** query window (432 Parquet files) as a
  service-protection limit. It can be raised at startup, but Core has **no compactor** — data lands as
  10-minute Parquet files and never gets consolidated, so multi-day/multi-month scans read thousands of
  small files (slow + memory pressure). Enterprise adds the compactor + single-series indexing, i.e.
  real historical-TSDB behaviour. Lenti's `query_forecast_accuracy_ranking` scans **90 days** and
  `query_measurement_count` scans **365 days** — squarely in the range Core is explicitly not built for.
- **Licence catch**: Enterprise's free tier is **at-home, non-commercial** and rate-limited; monetised
  use needs a paid licence. This is why D1 is a product decision, not a technical one.
- **Sources**:
  - [Announcing InfluxDB 3 Enterprise free for at-home use + Core's 72-hour limitation — InfluxData](https://www.influxdata.com/blog/influxdb3-open-source-public-alpha-jan-27/)
  - [Query data — InfluxDB 3 Core docs](https://docs.influxdata.com/influxdb3/core/get-started/query/)
  - [Config options (file-count limit) — InfluxDB 3 Core docs](https://docs.influxdata.com/influxdb3/core/reference/config-options/)

## R3 — Client library

- **Decision**: Replace `influxdb-client` (`pyproject.toml:15`, the 2.x lib) with the v3 Python client
  **`influxdb3-python`** (import `influxdb_client_3`).
- **Rationale**: The 2.x lib speaks the 2.x `/api/v2/query` Flux endpoint; it does not do v3 SQL. The v3
  client writes via **line protocol** and **reads returning a `pyarrow.Table`** (queried over Arrow
  Flight / FlightSQL). Return shapes downstream are our own `dict`/`list[dict]`, so the pyarrow Table is
  converted to those inside each method — the conversion is the per-method work.
- **VERIFY**: exact `influxdb3-python` API surface for (a) per-query timeout, (b) synchronous vs async
  query call, (c) batching/`write_precision` on writes, (d) whether one client object can carry three
  timeout tiers or whether we instantiate three (mirrors today's `_client`/`_slow_client`).
- **Impact on the async pattern**: unchanged. Today's methods are sync and wrapped by callers in
  `asyncio.to_thread` (per `04-constraints.md` T07/T08). The v3 client's sync query is wrapped the same
  way; if only an async client exists, methods stay sync internally by running it — no caller changes.

## R4 — Query language: SQL vs InfluxQL

- **Decision**: **SQL (DataFusion).** (D2)
- **Rationale**: `query_forecast_accuracy_ranking` joins 90 days of actuals to forecasts on hourly
  timestamps; the replay/snapshot/`*_for_hour` methods pivot fields into columns. SQL does joins,
  `PIVOT`-equivalents (conditional agg), and window functions natively. InfluxQL cannot join and would
  push that work back into Python. The simple `latest`/`history` methods are equally easy either way.
- **Alternatives**: InfluxQL — rejected for the joins; would increase Python-side stitching and diverge
  from the "reproduce exactly" goal by changing where the logic lives.

## R5 — Data migration path

- **Decision**: **Export line protocol from 2.7 → bulk load into v3** (D3). No in-place upgrade (v3
  cannot read 2.x TSM).
- **Method**: `influxd inspect export-lp` (2.x, offline export to line protocol, per bucket/measurement,
  supports time-range chunking) → load into v3 via the write API / `influxdb3` CLI.
- **VERIFY (R5a)**: Fedora box disk + RAM headroom to run 2.7 and v3 side-by-side during dual-write, and
  to hold the exported line-protocol dump (the `wind_forecast_grid` measurement dominates: ~1.17 M
  points/run × history). Consider exporting grid data in time-chunks to bound peak disk.
- **VERIFY (R5b)**: v3 write-API ingest throughput for the bulk load, and the chunk size that avoids the
  same read-timeout class of failure the 5000-point write chunking fixed on 2.x.

## R6 — Data-model mapping

See `data-model.md`. Summary: 2.x **bucket → v3 database**; **measurement → table**; **tag → tag
column** (part of series key); **field → field column**. The four measurements (`weather_data`,
`weather_forecast`, `wind_forecast_grid`, `rule_decisions`) and their tag/field sets from
`architecture.md` carry over 1:1. `init_date` / `valid_time` / `_source` semantics are unchanged.

## R7 — Method inventory & difficulty (the actual work)

All in `database/influx.py`. Grouped by rewrite difficulty. Line numbers are the `def`.

### Writes → line protocol (6) — mechanical
`write_measurements:72`, `write_forecast:808`, `write_forecast_grid:879` (keep chunking),
`write_forecast_deviations:1390`, `write_grid_forecast_deviations:1432`, `write_foehn_status:1834`.

### Simple reads → SQL SELECT / DISTINCT-ON / window (≈12) — low risk
`query_latest:118`, `query_latest_all_stations:153`, `query_latest_for_stations:198`,
`query_observation_snapshot_for_stations:238`, `query_history:541`, `query_history_virtual:487`,
`query_history_all_stations:619`, `query_history_for_stations:1209`, `has_measure:657`,
`query_observations_for_hour:1476`, `query_latest_virtual:406`, `_members_established_for_window:422`.

### Analytic / high-logic (≈9) — must reproduce math exactly (FR-004)
- `query_forecast_replay:1023` + `_latest_forecast_init_dates:997` — the two-step latest-init-date scan.
- `query_forecast_for_stations:1101` — swissmeteo-≤24h-else-latest source preference.
- `query_forecast_snapshot_for_stations:280`.
- `query_forecast_accuracy:1268` and **`query_forecast_accuracy_ranking:1613`** — 90-day join, MAE/bias,
  **circular error for `wind_direction`** (modular distance, not raw diff).
- `query_extremes_for_period:715`, `query_decision_history:686`, `query_decision_history_multi:767`,
  `query_forecast_grid:923`, `query_grid_forecasts_for_hour:1565`, `query_forecasts_for_hour:1518`,
  `query_foehn_pressure_history:329`.

### Meta / introspection (≈4) — VERIFY v3 equivalents exist
- `query_data_bounds:572`, `query_measurement_count:1721`, `query_daily_ingestion:1743` — likely map to
  v3 system tables / SQL `MIN(time)`/`MAX(time)`/`COUNT`. **VERIFY** system-table names.
- `query_storage_bytes:1772` — scrapes 2.x `storage_shard_disk_size_bytes` from `/metrics`. **v3 metric
  names differ**; rewrite against v3 metrics or return `None` gracefully. Stats-page-only, non-load-
  bearing.

### `has_forecast_deviation_data:1699` — simple COUNT ≥ threshold. Low risk.

## R8 — Test strategy (proving FR-004)

- **Golden-output diff**: capture a representative production data slice; run each method against a
  2.7-backed client and the v3-backed client; assert equal (float tolerance). This is the acceptance
  gate, independent of `FakeInflux`.
- **`FakeInflux` unchanged**: it duck-types the return contracts, not Flux. If contracts hold (FR-001),
  the existing `tests/backend/` suite passes untouched — that is itself evidence the boundary held.
- **Decision invariance**: re-run `test_rules_evaluator.py` / public-map tests against v3-shaped returns
  to confirm the v1.20.0 green-requirement fail-safe and no-data mapping are preserved.

## R9 — Config & connection

- `InfluxDBConfig` (`config.py:8`) today: `url`, `token`, `org`, `bucket`, `timeout`,
  `slow_query_timeout`. v3 drops `org`, renames `bucket` → `database`; token model differs.
- **VERIFY**: v3 auth token creation/format on the target instance; whether `ranking_query_timeout`
  (300 s, added for the accuracy ranking) is still needed given DataFusion performance — keep it as a
  config knob regardless.
- Only `config.yml.example` is committed (secrets stay out — `04-constraints.md`).

## Open VERIFY checklist (carried into Phase 1)

- R3: `influxdb3-python` timeout/async/write API specifics.
- R5a/R5b: Fedora headroom + bulk-load throughput/chunking.
- R7-meta: v3 system tables for bounds/count/ingestion; v3 metrics for storage bytes.
- R9: v3 token/auth + database creation on the friend's instance.
- **D1 (blocking, non-technical): user's monetisation intent for `lenti.cloud`.**
