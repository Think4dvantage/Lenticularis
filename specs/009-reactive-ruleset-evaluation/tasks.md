# Tasks: Reactive Ruleset Evaluation

**Phase**: 3 — Tasks · **Date**: 2026-08-06
**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)
**Target**: v1.22.6 → **v1.23.0**

---

## Summary

- **Total tasks**: 23
- **Parallel opportunities**: 7 tasks marked `[P]`
- **MVP scope**: Phase 1 + Phase 2 + **Phase 3 (US1 only)** — live decisions become reactive.
  Ships as a coherent, deployable improvement without any forecast-cache work.
- **Test tasks included** — plan §8 specifies them explicitly.

## Dependencies

```
Phase 1 (Setup)
    ↓
Phase 2 (Foundation) ──────────────┬──────────────┐
    ↓                              │              │
Phase 3 — US1 live reactive        │              │
    ↓                              ↓              │
Phase 4 — US2 forecast horizon ────┘              │
    ↓                                             │
Phase 5 — US2b replay reads cache                 │
    ↓                                             ↓
Phase 6 — US3 no-regression ──────────────────────┘
    ↓
Final Phase — Polish
```

US2b (Phase 5) **requires** US2 (Phase 4) — there is nothing to read until the horizon is written.
US3 (Phase 6) verifies all prior phases and must run last.

---

## ⚠️ Plan deltas — read before starting

Five things were verified against current `main` (2026-08-06) that `plan.md` (2026-08-03) either
states differently or does not mention. Each is reflected in the tasks below.

| # | Finding | Impact |
|---|---|---|
| **D1** | **Return-shape mismatch.** `run_forecast_evaluation` returns `list[dict]` with keys `valid_time` / `decision` / `condition_results` (`evaluator.py:707-716`). `write_decisions_batch` expects `list[tuple[str, str, list[dict]]]` (`evaluator.py:937-941`). Plan §3's diagram implies they compose directly — **they do not.** | Explicit adapter step — **T014** |
| **D2** | **Flux `range()` stops at `now()` by default.** `query_decision_history` uses `range(start: -{hours}h)` with no `stop:` (`influx.py:704-711`). Forecast decisions are stored at **future** `valid_time`s, so a copy-paste of that method returns **nothing, silently**. Plan §4.3's signature already takes `start`/`end`, but the trap is unstated. | Explicit `stop:` — **T006** |
| **D3** | **A second removal site the plan misses.** Besides the `IntervalTrigger` registration (`scheduler.py:342-349`), `scheduler.py:424-429` fires `call_later(jitter, _trigger_now("collector_ruleset_evaluator"))` at startup. This is today's de-facto FR-008 boot pass and **also** references the job id — leaving it raises once the job is gone. | **T009** removes both; **T012** re-homes the boot pass |
| **D4** | **`_maybe_notify` is a scheduler method.** `_run_ruleset_evaluator` calls `self._maybe_notify(rs, decision, db)` (`scheduler.py:507` → `:536`), which reads `self._cfg.smtp`. Relocating the evaluation loop into `rules/reactive.py` or `main.py` would strand FR-006's notification path. | **Deviation from plan T05** — see below |
| **D5** | **`FakeInflux` cannot observe writes.** `write_decision`/`write_decisions_batch` reach `influx._write_api` + `influx._cfg` inside a `try/except` that logs and swallows (`evaluator.py:976-989`, `:1008-1019`). `FakeInflux` has neither attribute, so writes raise `AttributeError` internally and are silently discarded — a "was the horizon written?" test would **pass vacuously**. | Recording write stub — **T007** |

### Deviation from plan T05 (because of D4)

Plan T05 says *"**Remove** `_run_ruleset_evaluator`"*. Doing so literally would strand
`_maybe_notify`, `self._influx`, `self._virtual_members`, `self._session_factory` and
`self._collector_health` — all of which the evaluation loop needs and all of which live on
`CollectorScheduler`.

**Instead**: keep the method on the scheduler and *generalise* it to accept an optional station
filter; remove only the **trigger registration** (which is what FR-005 actually requires — "the
existing fixed-interval scheduled re-evaluation is retired"). `rules/reactive.py` still gets
exactly the pure, testable surface plan T01 specifies (reverse lookup + in-flight guard) and
nothing more. Plan T01 / T02 / T03 / T04 / T06 are unaffected.

**Health key stays `ruleset_evaluator`** (not renamed to `ruleset_reactive` as plan §6 suggests):
`stats.html:802` renders health rows generically (`c.interval_minutes != null ? … : '—'`) and
nothing in `static/` or the i18n files references the key by name — so setting
`interval_minutes: None` renders `—` with **zero frontend or i18n change**. A rename would be
churn for no user-visible gain.

---

## Phase 1 — Setup

- [x] T001 Record the baseline: run `.venv/Scripts/python.exe -m pytest tests/backend -q` and note the passing count (expected **197** per `06-testing-conventions.md`). Every later phase compares against this number.
- [x] T002 [P] Add a `MEASUREMENT_DECISIONS_FORECAST = "rule_decisions_forecast"` constant alongside the existing `MEASUREMENT_*` block in `src/lenticularis/database/influx.py`, so no task below hardcodes the measurement name as a string literal.

---

## Phase 2 — Foundation

**Blocking prerequisites for every user story.** Nothing here changes runtime behaviour on its own.

- [x] T003 Create `src/lenticularis/rules/reactive.py` with `affected_ruleset_ids(db, station_ids, virtual_members) -> set[str]` — the plan §2.2/§2.3 reverse lookup. One `select(distinct(RuleCondition.ruleset_id)).where(RuleCondition.station_id.in_(ids) | RuleCondition.station_b_id.in_(ids))`, never a per-station loop (NFR-001). Invert `virtual_members` (`{canonical: [members…]}` → `{member: canonical}`) **before** the query and expand the incoming set through it, so a physical member station reporting still resolves the ruleset that references its canonical id (FR-003).
- [x] T004 Add the in-flight coalescing guard to `src/lenticularis/rules/reactive.py` — module-level `set[str]` + `threading.Lock`, exposing `try_claim(ruleset_id) -> bool` and `release(ruleset_id)` exactly as plan §7.1 specifies. Self-draining, so no bound is needed; note that in a comment since `04-constraints.md` otherwise mandates a max size on module-level caches.
- [x] T005 [P] Extend `write_decisions_batch` in `src/lenticularis/rules/evaluator.py` with `measurement: str = "rule_decisions"` and thread it into `Point(measurement)` at `evaluator.py:963`. Default preserves today's behaviour so the `run_history_backfill` caller is untouched (plan §4.1).
- [x] T006 [P] Add `query_forecast_decisions_for_ruleset(self, ruleset_id, start, end) -> list[dict]` to `src/lenticularis/database/influx.py`, mirroring `query_decision_history` (`:699`) but against `MEASUREMENT_DECISIONS_FORECAST`. **Per D2: pass an explicit `stop:` in `range()`** — the default is `now()` and every point this reads is in the future. Use a plain `r.ruleset_id == "…"` filter via `_flux_str()`; **never `contains()`** (the v1.22.6 bug class, `04-constraints.md`).
- [x] T007 [P] Extend `FakeInflux` in `tests/backend/conftest.py`: add a `query_forecast_decisions_for_ruleset` stub returning `[]`, plus — **per D5** — a recording `_write_api` (an object whose `.write(bucket, org, record)` appends to a list) and a minimal `_cfg` with `bucket`/`org`. Without these, any test asserting a decision batch was written passes without writing anything.

---

## Phase 3 — Live decisions track data, not the clock [US1]

**Goal**: A ruleset is re-evaluated the moment an observation collector touching one of its
stations finishes, and never on a timer.

**Independent test criteria**: With the interval job gone, completing a single observation
collector run causes exactly the rulesets referencing that collector's stations to be
re-evaluated — and no others. A restart still leaves every ruleset with a fresh decision.

- [x] T008 [US1] Generalise `_run_ruleset_evaluator` in `src/lenticularis/scheduler.py:485` to `evaluate_rulesets(self, station_ids: set[str] | None = None)`. `None` = today's every-ruleset pass (used by the boot pass, T012). A non-empty set filters via `affected_ruleset_ids` (T003) and claims each id via `try_claim`/`release` (T004, in a `finally`). The existing body — `run_evaluation` → `write_decision` → `self._maybe_notify` → per-ruleset `try/except` → health update — is otherwise **unchanged** (D4, FR-006).
- [x] T009 [US1] Remove the fixed-interval trigger from `src/lenticularis/scheduler.py` (FR-005): delete the `add_job(..., IntervalTrigger(minutes=10), id="collector_ruleset_evaluator", …)` registration at `:342-349` **and** — per D3 — the `call_later(… _trigger_now("collector_ruleset_evaluator"))` block at `:424-429`. Leaving the latter raises once the job id no longer exists.
- [x] T010 [US1] Repurpose the `ruleset_evaluator` health entry in `src/lenticularis/scheduler.py:328-341`: keep the key (see deviation note), set `"type": "derived"` and `"interval_minutes": None`, and have `evaluate_rulesets` record `last_triggered_at` / `last_affected_count` / `last_error` per plan §6. Registration must no longer be gated on the job existing.
- [x] T011 [US1] In `src/lenticularis/api/main.py`, compose the reactive live evaluator **into** the existing `on_collector_run` callback (plan §2.1). Wrap `_make_registry_updater`'s callable and the new `scheduler.evaluate_rulesets(station_ids)` call in one combined async callback assigned **once** at `:177`. ⚠️ A second `scheduler.on_collector_run = …` assignment silently discards the registry updater — the hook is single-slot, not a listener list. Derive `station_ids` from `await collector.get_stations()`, run the DB/Influx work through `asyncio.to_thread`, and on failure `logger.exception` — never a silent `pass` (`04-constraints.md`).
- [x] T012 [US1] In `src/lenticularis/api/main.py`'s lifespan, run one full `scheduler.evaluate_rulesets()` pass (no filter) as a background task after `scheduler.start()`, replacing the boot pass removed in T009 (FR-008). Keep it off the critical path so startup latency is unchanged.
- [x] T013 [US1] Create `tests/backend/test_reactive_evaluation.py` covering: a ruleset referencing a **canonical** station id is returned when a *member* of that virtual cluster is the station reported as updated (FR-003 — plan §8 calls this the single highest-value test); a ruleset whose stations are untouched is **not** returned (FR-004); a zero-condition ruleset never appears (FR-009); `try_claim` returns `False` for a second concurrent claim and `True` again after `release` (FR-007); the boot pass evaluates every ruleset with conditions exactly once. Use `SimpleNamespace` duck-typing per `06-testing-conventions.md`.

---

## Phase 4 — Forecast decisions track forecast updates [US2]

**Goal**: A forecast collector run recomputes and **stores** the entire forecast horizon for every
affected ruleset, in one pass each.

**Independent test criteria**: After a simulated forecast run, `rule_decisions_forecast` holds one
point per forecast hour per affected ruleset, readable back through T006.

- [x] T014 [US2] Add `evaluate_rulesets_forecast(self, station_ids)` to `src/lenticularis/scheduler.py`: reverse-lookup (T003) + in-flight claim (T004), then per affected ruleset call `run_forecast_evaluation(rs, self._influx, horizon_hours=120)` and write via `write_decisions_batch(rs, steps, self._influx, measurement=MEASUREMENT_DECISIONS_FORECAST)`. **Per D1, insert the adapter** — `run_forecast_evaluation` yields `list[dict]`, `write_decisions_batch` consumes `list[tuple]`: `[(s["valid_time"], s["decision"], s["condition_results"]) for s in steps]`. Always write the **full** horizon so last-write-wins overwrites the prior model run in place (plan §4.2).
- [x] T015 [US2] In `src/lenticularis/api/main.py`, compose `evaluate_rulesets_forecast` into the existing `on_forecast_run` callback alongside `_make_forecast_rewarmer` (`:180`) — same single-slot composition discipline and `asyncio.to_thread` / `logger.exception` rules as T011. Gate on the same `health["status"] == "ok" and last_measurement_count > 0` condition the rewarmer already uses, so a failed forecast run does not trigger a pointless horizon rewrite.
- [x] T016 [US2] Extend `tests/backend/test_reactive_evaluation.py`: the D1 adapter maps all three keys in order; a full horizon written via `write_decisions_batch(..., measurement="rule_decisions_forecast")` is read back intact by `query_forecast_decisions_for_ruleset`; the default `measurement` argument still writes `rule_decisions` (T005 regression guard); the generated Flux carries an explicit `stop:` and no `contains()` (D2 + `04-constraints.md`) — mirror the assertion style already in `tests/backend/test_influx_query_clients.py`.

---

## Phase 5 — Replay stops depending on live query latency [US2b]

**Goal**: Scrubbing/playing forecast time reads a precomputed decision instead of querying live
per frame — retiring the v1.22.6 root cause rather than its symptom.

**Independent test criteria**: `GET /api/rulesets/{id}/evaluate?at_time=…&forecast=true` returns
the precomputed decision when one exists, and still returns a correct decision when none does.

- [x] T017 [US2b] In `src/lenticularis/api/routers/rulesets.py`, change the `forecast` branch of `_evaluate_at` (`:226-228`) to try `influx.query_forecast_decisions_for_ruleset` for a window bracketing `at_time` first, and fall through to the existing `run_forecast_evaluation_at(rs, influx, at_time)` on an empty result. ⚠️ **Do not delete the live path** (plan §7.2) — a ruleset created between two forecast runs, or one whose horizon write failed, must still resolve on demand. The response shape must be byte-identical either way; the caller cannot tell which path served it.
- [x] T018 [US2b] Extend `tests/backend/test_reactive_evaluation.py`: a cache **hit** returns the stored decision without calling `run_forecast_evaluation_at` (assert via monkeypatched spy); a cache **miss** (`[]`) still produces a correct decision through the live fallback — the regression guard proving T017 did not remove it; both paths produce the same response shape. Reuse the linked-landing-halo fixtures from `tests/backend/test_forecast_evaluation_at.py` so the halo keeps evaluating in the same mode as its primary (specs/archive/007).

---

## Phase 6 — No functional regression for existing consumers [US3]

**Goal**: Decision history, email notifications, and the health dashboard behave exactly as they
did — only the trigger changed.

**Independent test criteria**: The interval job is absent from the scheduler, yet notifications
still fire once per real change and `rule_decisions` still accumulates observed history.

- [x] T019 [US3] Add the per-batch INFO log to `evaluate_rulesets` / `evaluate_rulesets_forecast` in `src/lenticularis/scheduler.py`: `[Lenti:reactive-eval] collector=%s stations=%d affected_rulesets=%d` (plan §6). A reactive path that silently stops triggering must be visible in logs, not merely "not erroring" — the same reasoning behind the thermal collector's coverage logging.
- [x] T020 [US3] Extend `tests/backend/test_reactive_evaluation.py` with the regression guards: the job id `collector_ruleset_evaluator` is absent from `scheduler._scheduler.get_jobs()` (FR-005); `_maybe_notify` is still invoked on a decision change and still suppressed when the decision is unchanged (FR-006 — the D4 risk, verified end to end); observed decisions still land in `rule_decisions`, not `rule_decisions_forecast` (§2.5's separation holds).

---

## Final Phase — Polish

- [x] T021 Bump `version` in `pyproject.toml` to **1.23.0** (plan §9 — behavioural change, not a patch). No static assets change in this feature, so the cache-key concern from `04-constraints.md` does not bite, but the version is still the release identity.
- [x] T022 [P] Sync documentation: add the v1.23.0 entry to `.ai/context/features.md` (and move the "Reactive Ruleset Evaluation" backlog entry out of Backlog); document `rule_decisions_forecast` in `.ai/context/architecture.md`'s InfluxDB Measurements section including the §4.2 known limitation (a shorter later horizon leaves stale points); update the Rules Engine Design section to note evaluation is now event-driven; update the milestone table in `README.md`.
- [x] T023 Final gate: `.venv/Scripts/python.exe -m pytest tests/backend -q` (expect T001's baseline + the new tests, all passing) and `ruff check --isolated src/ tests/` — plain `ruff check` cannot parse `pyproject.toml`'s Poetry caret in `requires-python` (`reference_dev_commands`, and tech debt noted in `features.md`).

---

## Traceability

| Requirement | Tasks |
|---|---|
| FR-001 live re-evaluation on observation | T003, T008, T011 |
| FR-002 forecast re-evaluation | T003, T014, T015 |
| FR-002a whole-horizon precompute | T005, T014 |
| FR-003 virtual/deduplicated station expansion | T003, T013 |
| FR-004 no evaluation without a dependency | T003, T013 |
| FR-005 fixed interval retired | T009, T020 |
| FR-006 downstream consumers unchanged | T008, T019, T020 |
| FR-007 burst coalescing | T004, T008, T013 |
| FR-008 startup pass | T012, T013 |
| FR-009 zero-condition rulesets never triggered | T003, T013 |
| NFR-001 / NFR-003 no per-station loop | T003, T014 |
| NFR-002 bounded evaluation churn | T004, T008 |
| US2b replay reads precomputed | T006, T017, T018 |
