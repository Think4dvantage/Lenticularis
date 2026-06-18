# Tasks: Review Remediation

## Summary
- Total tasks: 23
- Phases: 4 (Security → Performance → Architecture/Correctness → Debt/Quality)
- Each task has a standalone spec file in this folder, paste-ready for a cheaper model.
- `[P]` = parallelizable (touches different files, no dependency on an unchecked task).

## Dependencies

```
Phase 1 (T01–T05)  ── security, do first, independent of each other
        │
Phase 2 (T06–T11)  ── perf; T10 depends on nothing but pairs with the T19 _source note
        │
Phase 3 (T12–T19)  ── T13 (pages router) should land before/with T14 (scheduler hooks)
        │             T12 (RFC7807) is independent; T15/T16/T17/T18/T19 independent
        │
Phase 4 (T20–T23)  ── T20 (tests/CI) ideally lands early so later refactors are gated,
                      but is sized larger; T21/T22/T23 independent
```

## Phase 1 — Security (must-fix)
**Goal**: Close the one Critical and the high-severity auth/XSS holes.

- [x] T01 [P] Harden Flux queries against injection + validate station/ruleset IDs — `database/influx.py`, `api/routers/stations.py` → `T01-flux-injection.md`
- [x] T02 [P] Fail-closed on placeholder/empty JWT secret at startup — `config.py`, `api/main.py` → `T02-jwt-secret-fail-closed.md`
- [x] T03 [P] Validate webcam URL scheme server-side + escape XSS sinks in frontend — `models/rules.py`, `static/ruleset-analysis.html`, `static/forecast-analysis.js` → `T03-webcam-url-and-xss.md`
- [x] T04 [P] Add security-header + CORS middleware — `api/main.py` → `T04-security-headers-cors.md`
- [x] T05 OAuth hardening: tokens out of URL, `email_verified` check, session-bound state — `api/routers/auth.py`, `static/oauth-callback.html` → `T05-oauth-hardening.md`

## Phase 2 — Performance (cheap, high-value)
**Goal**: Remove event-loop blocking and oversized payloads.

- [x] T06 [P] Add `GZipMiddleware` — `api/main.py` → `T06-gzip-middleware.md`
- [x] T07 Stop blocking the event loop in `async def` Influx handlers — `api/routers/stations.py`, `wind_forecast.py`, `foehn.py` → `T07-unblock-async-handlers.md`
- [x] T08 [P] Offload synchronous Influx writes off the event loop in the scheduler — `scheduler.py` → `T08-offload-scheduler-writes.md`
- [x] T09 [P] Batch the rule evaluator's per-station Influx fetch — `rules/evaluator.py` → `T09-batch-rule-evaluator.md`
- [x] T10 [P] Bound + lock the in-memory caches; route ranking query to the slow client; fix TTL docstring — `api/routers/stations.py`, `database/influx.py` → `T10-bound-lock-caches.md`
- [x] T11 [P] Enable SQLite WAL + `busy_timeout` — `database/db.py` → `T11-sqlite-wal.md`

## Phase 3 — Architecture & Correctness
**Goal**: Conform to the project's own conventions; fix two real bugs.

- [x] T12 [P] Add global exception handler + standardized response/RFC7807 error envelope — `api/main.py`, new `api/errors.py` → `T12-rfc7807-exception-handler.md`
- [x] T13 Move inline page routes out of `main.py` into a `pages` router — `api/routers/pages.py`, `api/main.py` → `T13-pages-router-extraction.md`
- [x] T14 Replace scheduler monkey-patching with real post-run hooks — `scheduler.py`, `api/main.py` → `T14-scheduler-hooks.md`
- [x] T15 [P] Reconcile migration docs + drop unused `alembic` dependency — `pyproject.toml`, `.ai/instructions/02-backend-conventions.md` → `T15-migration-docs-and-alembic.md`
- [x] T16 [P] Single-source the version; fix README casing; commit `poetry.lock` — `api/main.py`, `pyproject.toml`, `.gitignore` → `T16-version-readme-lockfile.md`
- [x] T17 [P] Convert legacy SQLAlchemy `query()` to 2.0 `select()` — `api/routers/auth.py`, `api/routers/rulesets.py` → `T17-legacy-query-to-select.md`
- [x] T18 [P] Fix silently-swallowed exceptions — `api/main.py`, `api/routers/ai.py` → `T18-swallowed-exceptions.md`
- [x] T19 [P] Fix two Influx correctness bugs (duplicate field key + `_source` dedup no-op) — `database/influx.py` → `T19-influx-correctness-bugs.md`

## Phase 4 — Debt & Quality (larger)
**Goal**: Establish a safety net and reduce duplication.

- [x] T20 Stand up a pytest harness + CI test job — `tests/`, `pyproject.toml`, `.github/workflows/` → `T20-test-harness-and-ci.md`
- [x] T21 De-duplicate collectors (shared `_to_float`/timestamp/wind-dir + concurrency) — `collectors/` → `T21-collector-dedup.md`
- [x] T22 De-duplicate frontend nav/bootstrap boilerplate — `static/` → `T22-frontend-bootstrap-dedup.md`
- [x] T23 [P] Replace remaining hardcoded UI strings with i18n keys — `static/ruleset-editor.html`, `static/foehn.html`, `static/i18n/*.json` → `T23-i18n-hardcoded-strings.md`

## Final note
Tests are deliberately their own task (T20) rather than embedded per-task: the codebase has no
harness today, so the first model session must build it before later tasks can be test-gated.
For T01–T19, the "Acceptance criteria" in each file are manual/curl checks.
