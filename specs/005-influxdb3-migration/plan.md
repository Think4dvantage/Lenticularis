# Implementation Plan: InfluxDB 2.7 → 3 Migration

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md) · **Data model**: [data-model.md](./data-model.md)
**Phase**: 2 — Plan · **Date**: 2026-07-21
**Blocked on**: D1 (edition/licensing). The plan below is edition-agnostic between Enterprise-free and
Enterprise-paid; if D1 resolves to "stay on 2.7", this plan is shelved.
**Next step**: `tasks.md` (only after D1)

---

## Technical Context

| Concern | Choice |
|---|---|
| Engine | InfluxDB 3 **Enterprise** (edition per D1); Core rejected for the 90/365-day workload |
| Query language | **SQL** (DataFusion) — R4/D2 |
| Client lib | `influxdb-client` → **`influxdb3-python`** (`influxdb_client_3`) — R3 |
| Reads | v3 returns `pyarrow.Table`; each method converts to today's `dict`/`list[dict]` |
| Writes | Line protocol via v3 write API; keep grid chunking (re-sized per R5b) |
| Persistence (SQLite) | **Untouched** |
| App code above `influx.py` | **Untouched** (FR-001/FR-007) — the whole point |
| Data migration | Export line protocol from 2.7 → bulk load v3 (D3); dual-write cutover (D4) |
| Config | `InfluxDBConfig`: drop `org`, `bucket`→`database`, v3 token model (R9) |

**Architecture approach**: `database/influx.py` is a façade — ~30 methods with stable dict/list
contracts, and Flux confined behind them. The migration swaps the *inside* of the façade (client +
query language + write encoding) while holding the *outside* (signatures + return shapes) frozen. That
frozen boundary is what keeps routers, `rules/evaluator.py`, `scheduler.py`, and `FakeInflux` untouched,
and is what the golden-output test asserts.

## Constitution Check

Per `.ai/instructions/00-ai-usage.md`.

| # | Principle | Status | Notes |
|---|---|---|---|
| 1 | Read before acting | ✅ Pass | Method inventory + line numbers taken from `influx.py`; v3 limits verified against InfluxData docs (R2), not memory |
| 2 | Plan before building | ✅ Pass | This document; no code until D1 resolved and an explicit go-ahead |
| 3 | Minimal scope | ✅ Pass | One file rewritten internally + config + deps; no app-layer changes, no new features |
| 4 | Tool-agnostic instructions | ✅ Pass | All artifacts under `specs/`; nothing outside `.ai/`-governed layout |
| 5 | Keep docs in sync | ✅ Pass | Phase 5 updates `architecture.md` (InfluxDB section), `features.md`, README/PLANNING, `config.yml.example` |
| 6 | No secrets committed | ✅ Pass | Only `config.yml.example`; real token stays in gitignored `config.yml` |
| 7 | Prod is off-limits | ⚠️ Context | `lenti.cloud` runs on a friend's Fedora box, not the IaC homelab. The dual-write/cutover touches that live instance — **treat as prod**: staged, reversible, user-driven. No unattended destructive steps. |

**No blocking constitution violations.** Item 7 is a caution, not a violation — the D4 cutover is
explicitly reversible and gated on the golden-output diff.

### Additional constraint compliance (`04-constraints.md`)

| Constraint | Compliance |
|---|---|
| No Alembic / SQLite migration change | N/A — no SQLite change |
| No `os.environ` | Config still via `get_config()` |
| Async event loop not blocked (T07/T08) | v3 sync calls stay wrapped in `asyncio.to_thread` by callers — unchanged |
| Batch, don't loop (T09) | Batch methods (`query_latest_for_stations` etc.) stay batch in SQL |
| InfluxDB write integrity (T19) | Enforced in `data-model.md` invariants; string/float typing established deliberately |
| Version bump on static-asset change | **None expected** — no `static/` change. If none, **no `pyproject.toml` bump** |

## Data Model Summary

Bucket→database, measurement→table, tags/fields carry 1:1. Four measurements unchanged. Full detail and
per-measurement parity checks in `data-model.md`.

## File Structure

**Modify**

| File | Change |
|---|---|
| `src/lenticularis/database/influx.py` | Rewrite internals of all ~30 methods: swap client, SQL queries, line-protocol writes. **Public signatures and return contracts frozen.** |
| `src/lenticularis/config.py` | `InfluxDBConfig`: drop `org`, `bucket`→`database`, v3 token model; keep three timeout knobs |
| `pyproject.toml` | Dependency `influxdb-client` → `influxdb3-python`. (Not the app-version bump — no static assets change) |
| `config.yml.example` | v3 connection keys (database, token, url) |
| `docker-compose.yml` / `.dev.yml` | Update the InfluxDB connection comment/env if referenced; **the DB itself is external, still not managed here** |
| `.ai/context/architecture.md` | InfluxDB Client + Measurements sections: v3 engine, SQL, database term, client tiers |
| `.ai/context/features.md` | New milestone entry |
| `README.md` / `PLANNING.md` | Tech-stack line (InfluxDB 2.x → 3), per `sync.md` |

**Create**

| File | Purpose |
|---|---|
| `tests/backend/test_influx3_parity.py` | Golden-output diff harness (2.7 vs v3, same data) — the FR-004 gate |
| `scripts/` migration runbook (or `specs/005-.../runbook.md`) | Export/load + dual-write + cutover + rollback steps (operator doc, not app code) |

**Untouched (asserted, not assumed)**: every `api/routers/*`, `rules/evaluator.py`, `scheduler.py`,
`services/*`, `tests/backend/conftest.py` `FakeInflux`, all `static/*`.

## Implementation Phases

Phases 1–2 are code and can be built/tested before the live box is touched. Phases 3–5 touch the live
instance and are gated, reversible, user-driven.

### Phase 0 — Resolve D1 (blocking, no code)
Get the monetisation answer → pick edition. If "stay on 2.7", stop here. Then close the R-VERIFY items
(R3 client API, R5 headroom, R7-meta system tables, R9 token) against the real v3 instance and docs.

### Phase 1 — Rewrite `influx.py` internals + config
1. Add `influxdb3-python`, drop `influxdb-client` in `pyproject.toml`; update `InfluxDBConfig`.
2. Rewrite the 6 writes to line protocol (keep grid chunking; re-size per R5b).
3. Rewrite the ~12 simple reads (SELECT / DISTINCT-ON / window) → dict/list conversion from pyarrow.
4. Rewrite the ~9 analytic reads. Reproduce **exactly**: replay two-step init-date, source preference,
   and the circular `wind_direction` error in the accuracy ranking.
5. Rewrite the ~4 meta/introspection reads; `query_storage_bytes` against v3 metrics or graceful `None`.
6. Preserve the three timeout tiers on the v3 client(s).

*Verifiable*: unit-level, method by method, against a scratch v3 instance seeded with fixture data.

### Phase 2 — Golden-output parity test
1. `test_influx3_parity.py`: for a captured data slice, assert each method's v3 result equals the
   2.7 result (float tolerance). Wind-direction circular error and no-data→absent-key mapping are
   explicit cases.
2. Run the **existing** `tests/backend/` suite unchanged — it passing is proof the façade held (FR-001).

*Verifiable*: `poetry run pytest tests/backend -q` green; parity test green.

### Phase 3 — Stand up v3 + migrate history (live box, maintenance window)
1. Install/start InfluxDB 3 Enterprise alongside 2.7 on the Fedora box; create the database + token.
2. `influxd inspect export-lp` per measurement (grid in time-chunks); bulk-load into v3.
3. Per-measurement parity checks from `data-model.md` (row counts, `MIN/MAX(time)`, spot samples).

*Verifiable*: parity checks pass for all four measurements including full grid history.

### Phase 4 — Dual-write, then flip reads (D4)
1. Point collectors' writes at **both** 2.7 and v3 for a verification window (config toggle / dual
   client). 2.7 stays authoritative for reads.
2. When the window is clean and Phase 2 parity holds against live v3, **flip reads** to v3 (config).
3. Watch decisions/accuracy/stats for a hold period; rollback = flip reads back to 2.7 (still dual-fed).

*Verifiable*: production decisions/accuracy unchanged over the hold period; rollback tested at least once
in staging/scratch before relying on it.

### Phase 5 — Decommission + docs
1. Stop dual-write, decommission 2.7 only after rollback confidence is high.
2. Sync docs: `architecture.md`, `features.md`, README/PLANNING, `config.yml.example` (per `sync.md`).

## Dependencies

- `influxdb3-python` (`influxdb_client_3`) — replaces `influxdb-client`.
- InfluxDB 3 Enterprise binary on the Fedora box (edition per D1).
- 2.x `influxd inspect export-lp` available on the current instance for export.
- Disk/RAM headroom on the Fedora box for side-by-side run + export dump (R5a).

## Risk & Mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **D1 unresolved / monetisation makes Enterprise-free invalid** | **Blocking** | Resolve D1 first. If commercial, either paid licence or stay on 2.7 — do not start Phase 1 on a false premise |
| 2 | **Core chosen to save licence cost, then 90/365-day queries die** | High | R2 rules Core out explicitly; the parity test on the ranking/count queries would fail loudly, but the intent is to not go there |
| 3 | **Accuracy numbers shift** (circular wind error, join semantics) | High | Golden-output parity test (Phase 2) is the acceptance gate, with wind-direction as an explicit case |
| 4 | **No-data mapping changes** → breaks v1.20.0 green-requirement fail-safe | High | Explicit parity case: v3 empty result must produce the same absent-key dict; re-run evaluator tests |
| 5 | **A method contract subtly changes** → forces an app-layer edit (FR-001 breach) | Medium | The existing `tests/backend/` suite passing unchanged is the tripwire; if a router needs editing, stop and fix the façade |
| 6 | **Grid history export/load OOMs or times out** (~1.17 M pts/run) | Medium | Time-chunked export; re-measured load chunk size (R5b); bounded peak disk (R5a) |
| 7 | **Touching the live friend's-box instance** | Medium | Treat as prod (constitution 7): dual-write keeps 2.7 authoritative; reads flip only after parity; rollback tested |
| 8 | **v3 meta/metrics gaps** (`query_storage_bytes`, bounds, counts) | Low | Non-load-bearing stats; graceful `None` / system-table equivalents (R7-meta) |
| 9 | **`influxdb3-python` API differs from assumption** | Low | R3 VERIFY items closed against real client before Phase 1 coding |

## Follow-up (out of scope, flagged)

- The `pyproject.toml` `requires-python = "^3.11"` PEP 621 bug (breaks plain `ruff check`) is adjacent
  but **not** bundled here — fix it in its own change to keep this migration reviewable.
- Thermal-grid backlog integration is unrelated and must not ride along.
