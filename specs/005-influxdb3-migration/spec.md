# Feature: InfluxDB 2.7 → InfluxDB 3 Migration

**Created**: 2026-07-21
**Status**: **Shelved (2026-07-23)** — D1 resolved to *defer*. Spec/plan complete; not proceeding to
`tasks.md`. Reactivate when the monetisation question closes (see D1).
**Next step**: none until D1 reopens. If it resolves non-commercial → Enterprise-free path in `plan.md`
Phase 0. Until then, **stay on InfluxDB 2.7** (still supported).

## Overview

The dedicated, single-tenant InfluxDB **2.7** instance backing `lenti.cloud` (currently on a friend's
Fedora server while the homelab is down) is migrated to **InfluxDB 3**. The motivation is longevity,
not a feature: InfluxData's active development is on the 3.x line, and **Flux — which the entire data
layer is written in — is in maintenance mode and is not supported at all in InfluxDB 3**. Staying on
2.7 keeps a supported-but-terminal engine; moving to 3 is the only path to "far-future updates."

The migration is **contained to one file by design**. `database/influx.py` is the sole module that
touches Flux (`from(bucket:)` appears in 32 places; ~30 public query/write methods). Every router, the
rules evaluator, the scheduler, and the `FakeInflux` test double consume it through methods that return
plain Python `dict`/`list[dict]`. If those signatures and return contracts are held **byte-identical**,
nothing above `influx.py` changes — this is the load-bearing constraint the whole plan is built on.

## The blocking decision (read first)

Migrating the *code* is a contained rewrite. Migrating the *product* onto a v3 edition that can
actually serve Lenticularis's workload is a **licensing decision**, and it gates everything:

| Edition | Long-range query capability | Licence | Fit for Lenti |
|---|---|---|---|
| **InfluxDB 3 Core** (OSS, free) | Default **72 h** query window (432 Parquet files); raisable but **no compaction** → many small 10-min files → degraded performance and memory pressure on multi-day scans | Apache 2.0, unrestricted | **No.** Lenti runs 90-day accuracy joins (`query_forecast_accuracy_ranking`, `influx.py:1613`) and 365-day counts (`query_measurement_count`, `influx.py:1721`). Core is a "recent-data engine" by design. |
| **InfluxDB 3 Enterprise** | Compactor rewrites 10-min files into larger blocks + single-series indexing → full historical TSDB over months/years | **Free tier is "at-home, non-commercial use" only** (and rate-limited); **monetised / production business use requires a paid licence** | Fits the workload — but the free tier's terms collide with `lenti.cloud` being "potentially monetised" (`05-user-profile.md`). |

**D1 is therefore the gate:** *Will `lenti.cloud` remain non-commercial, or is monetisation on the
table?* The answer selects the edition, and the edition determines whether this migration is worth
doing at all versus staying on the still-supported 2.7. See **Decisions → D1**.

## User Stories

### P1 — The app behaves identically after the cutover

As a pilot, I want every traffic-light decision, station history chart, forecast, and accuracy number
to be identical before and after the database is swapped, so that the migration is invisible to me.

**Acceptance Criteria**:
- For a fixed input dataset, every one of the ~30 `InfluxClient` methods returns a result equal to
  what the 2.7-backed method returned (same keys, same numeric values within float tolerance).
- Rule decisions for the same station data are identical (proven by golden-output diff, not inspection).
- Forecast-accuracy MAE/bias — including the **circular error for `wind_direction`** — matches.
- No file above the `influx.py` boundary (routers, `rules/evaluator.py`, `scheduler.py`, `FakeInflux`)
  is modified.

### P2 — All history survives and long-range queries still perform

As the operator, I want the full historical weather/forecast/decision/grid data preserved and queryable
at the same ranges (90 d, 365 d), so that the accuracy dashboard and stats pages keep working.

**Acceptance Criteria**:
- 100% of series migrated from 2.7 to v3 (row-count parity per measurement, spot-checked timestamps).
- The 90-day `forecast-accuracy-ranking` and 365-day `measurement-count` queries complete within the
  existing timeout tiers (`influx.py` uses 10 s / 60 s / 300 s clients).
- The `wind_forecast_grid` measurement (~1.17 M points per run) migrates without truncation.

### P3 — The engine is on the actively-developed line

As the operator, I want Lenti on an InfluxDB line that receives updates for the foreseeable future, so
that I am not stranded on a maintenance-only engine and a maintenance-only query language (Flux).

**Acceptance Criteria**:
- Production runs on InfluxDB 3 (edition per D1), reachable by the app.
- 2.7 is decommissioned only after a verified cutover with a tested rollback path.

## Functional Requirements

- **FR-001**: Every public method of `InfluxClient` keeps its exact signature and return contract.
  Consumers above `influx.py` are not touched. This is verified, not assumed.
- **FR-002**: All ~30 methods are reproduced against InfluxDB 3, including the three that carry
  non-trivial logic: `query_forecast_replay` (two-step latest-init-date scan, `influx.py:1023`),
  `query_forecast_for_stations` (swissmeteo-≤24h source preference, `influx.py:1101`), and
  `query_forecast_accuracy_ranking` (90-day join + circular wind-direction error, `influx.py:1613`).
- **FR-003**: All historical data is migrated and queryable at the same time ranges as today.
- **FR-004**: Rule decisions and forecast-accuracy outputs are provably unchanged for identical input.
- **FR-005**: The write path (6 methods: `write_measurements`, `write_forecast`, `write_forecast_grid`,
  `write_foehn_status`, `write_forecast_deviations`, `write_grid_forecast_deviations`) produces
  equivalent series in v3, including the `weather_data` / `weather_forecast` / `wind_forecast_grid` /
  `rule_decisions` tag+field layout defined in `architecture.md`.
- **FR-006**: Cutover is reversible until 2.7 is decommissioned. A dual-write window keeps both engines
  current so reads can be flipped back if v3 misbehaves.
- **FR-007**: The three timeout tiers (standard 10 s, slow 60 s, ranking 300 s) are preserved as
  equivalent per-query timeouts on the v3 client.
- **FR-008**: `config.py InfluxDBConfig` is updated for v3 connection semantics (database name replaces
  `bucket`; auth-token model per v3). No secret is committed — only `config.yml.example` changes.

## Non-Functional Requirements

- **NFR-001 (Performance parity)**: Long-range analytic queries (90 d, 365 d) must not regress beyond
  the existing timeout tiers. On Core this is expected to **fail** without raising file limits (see D1);
  this is the crux of the edition choice, not an implementation detail to tune later.
- **NFR-002 (Operability)**: v3 must expose a health/readiness signal the container healthcheck and
  `/api/health` can use, and must be restartable without data loss (12-factor, per `05-user-profile.md`).
- **NFR-003 (No scope creep)**: No new app features, no new routes, no schema changes above `influx.py`.
  The thermal-grid backlog item and any query "improvements" are explicitly not part of this.

## Success Criteria

- A golden-output test proves the ~30 methods return equal results on the same data across the two
  engines.
- Rule decisions and accuracy numbers are byte-identical for a captured production sample.
- Full history is queryable on v3 at 90 d / 365 d within existing timeouts.
- Production is on InfluxDB 3, 2.7 decommissioned, with a rollback that was tested (not just written).

## Decisions

### D1 — Edition & licensing — **RESOLVED 2026-07-23: DEFER (stay on 2.7 for now).**

User's monetisation intent for `lenti.cloud` is **undecided / might monetise later**. Committing to a v3
edition now is premature both ways: the Enterprise free tier is non-commercial-only (a future monetise
would breach it), and a paid Enterprise licence is unjustified before the project earns. Core does not
fit the workload (below). **Chosen: option (d) — stay on the still-supported 2.7 line, keep this spec
shelved and ready.** Reactivate the moment the commercial question closes: non-commercial → option (a);
committed-commercial → weigh (b) paid Enterprise vs continuing on 2.7.

Original options (retained for the reactivation decision):

- **(a) Enterprise, free at-home tier** — fits the workload (compaction + historical indexing).
  **Valid only if Lenti stays non-commercial.** Rate-limited (terms TBD with InfluxData via Discord).
- **(b) Enterprise, paid licence** — fits the workload, no usage restriction. Cost unknown; introduces a
  recurring licence against a personal project.
- **(c) Core** — free/unrestricted, but a recent-data engine. Raising the 432-file limit lets old data
  be *queried* but without compaction the 90/365-day scans degrade badly (many small Parquet files).
  Realistically **does not serve Lenti's analytic queries**.
- **(d) Stay on 2.7** — the honest null option. 2.7 is still supported today and speaks Flux, so zero
  code work; but it is the maintenance-only line this migration exists to leave.

**Recommendation:** if Lenti stays non-commercial → **(a)**. If monetisation is real → **(d) stay on
2.7 for now** and revisit when a paid Enterprise licence is justified, because paying for a database
licence on a not-yet-earning project inverts the cost/benefit. **(c) Core is not recommended** for this
workload regardless. The migration below is written to be edition-agnostic between (a) and (b) — the
code and cutover are identical; only the licence and rate-limit differ.

### D2 — Query language: **SQL (DataFusion), not InfluxQL.**
Chosen over InfluxQL. The hard queries are pivots and joins (`query_forecast_accuracy_ranking` joins
90 days of actuals to forecasts; the replay/snapshot methods pivot fields into columns). SQL over
DataFusion expresses joins and window functions directly; InfluxQL cannot join and would force more
Python-side stitching. The simple `latest`/`history` methods are trivial in either. Resolved in
`research.md`.

### D3 — Data migration: **export line protocol from 2.7 → bulk-load into v3**, not in-place upgrade.
v3 cannot read 2.x TSM files. Export per measurement via `influxd inspect export-lp`, load via the v3
write API / `influxdb3` CLI. The `wind_forecast_grid` volume makes this the slowest step; it runs in a
maintenance window. Detail in `research.md` and `plan.md`.

### D4 — Cutover: **dual-write, flip reads, then decommission.**
Collectors write to both 2.7 and v3 for a verification window; history is backfilled by D3; reads flip
to v3 only after the golden-output diff passes; 2.7 stays until rollback confidence is high. Single
-tenant, so there is no other consumer to coordinate — the only actor is Lenti itself.

## Out of Scope

- Any change to code above `database/influx.py`. If a router or the evaluator needs to change, the
  method contract was not held and that is a bug in this migration, not a feature of it.
- New features, new routes, new measurements. The thermal-grid backlog item is unrelated.
- Query "optimisation" beyond reproducing current behaviour and meeting the existing timeouts.
- Refactoring the app-side `asyncio.to_thread` wrapping (the sync-client-in-async pattern is unchanged;
  the v3 client's sync methods are wrapped exactly as the 2.x ones are).
- Homelab restoration. Lenti stays on the friend's Fedora box for this work.

## Assumptions

- The v3 instance is dedicated to Lenti (confirmed) — so raising file limits, choosing databases, and
  scheduling maintenance windows affect no other service.
- The friend's Fedora box can run a second InfluxDB process (v3) alongside 2.7 during the dual-write
  window (disk + memory headroom to be confirmed in `research.md` R5).
- Historical data must be preserved in full (confirmed with user).
- `bcrypt`/JWT/SQLite and all non-Influx storage are untouched.

## Edge Cases

- **`query_storage_bytes` (`influx.py:1772`)** scrapes 2.x Prometheus `storage_shard_disk_size_bytes`
  from `/metrics`. v3's metrics names/shape differ — this method needs a v3-specific rewrite or becomes
  `None` gracefully. Non-load-bearing (stats page only).
- **`query_measurement_count` / `query_daily_ingestion` / `query_data_bounds`** — meta/introspection
  queries; verify v3 equivalents exist (system tables / `SHOW` equivalents) rather than assuming.
- **Circular error for `wind_direction`** in the accuracy ranking must reproduce the exact modular-
  distance formula, not a naive difference, or accuracy numbers shift (FR-004).
- **No-data semantics**: a missing station must still surface as "no data" to the evaluator exactly as
  today — a v3 empty result must map to the same absent-key `dict` the Flux path produced, or the
  v1.20.0 green-requirement fail-safe changes behaviour.
- **Grid write chunking**: the 5000-point chunking that fixed 2.x read-timeouts must have a v3
  equivalent sizing, re-measured against the v3 write API.
- **Duplicate field-key / `_source` dedup invariants** (T19 in `04-constraints.md`) must hold on v3.
