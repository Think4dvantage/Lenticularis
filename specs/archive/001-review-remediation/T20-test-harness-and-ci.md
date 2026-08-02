# T20 — Stand up a pytest harness + CI test job

**Severity:** High · **Phase:** 4 · **Model tier:** Larger / iterative (multiple sessions)

## Ground Rules
- Read `.ai/instructions/06-testing-conventions.md` (the target strategy) and `02-backend-conventions.md`.
- LF line endings only. No build step for the frontend. Land this **early** so later refactors are
  test-gated. Implement incrementally — get the harness green first, then add coverage.

## Problem
Zero tests exist. No `tests/` dir, no `pytest`/`pytest-asyncio` in `pyproject.toml`, and
`.github/workflows/` has only `docker-publish.yml` (no test/lint job). The conventions doc declares
testing "mandatory" with an 80% target. This is the largest debt item and the safety net every other
task needs.

## Fix — do in this order

### Step 1: Dev dependencies + config
Add to `pyproject.toml` `[dependency-groups] dev`:
```
"pytest (>=8,<9)",
"pytest-asyncio (>=0.24,<0.25)",
"httpx (>=0.28,<0.29)",        # already a runtime dep; fine to rely on it
"coverage[toml] (>=7,<8)",
```
Add a `[tool.pytest.ini_options]` block: `asyncio_mode = "auto"`, `testpaths = ["tests"]`.

### Step 2: Harness + fixtures (`tests/backend/conftest.py`)
- Build a fixture that creates the FastAPI `app` with an **in-memory SQLite** DB
  (`sqlite:///:memory:`) and a **fake/in-memory InfluxClient** stubbed onto `app.state.influx`
  (return canned dicts for `query_latest`, `query_latest_for_stations`, `query_history`, etc.). The
  scheduler and real InfluxDB must NOT start during tests — call the routers against the app object
  with `httpx.AsyncClient(transport=ASGITransport(app=app))`, bypassing `lifespan`, and set
  `app.state` manually in the fixture.
- Provide a helper to mint a valid JWT for an authenticated test user.

### Step 3: First high-value tests (critical paths)
Prioritize, one file each under `tests/backend/`:
1. `test_rules_evaluator.py` — `rules/evaluator.py`: green/orange/red for representative condition
   sets, worst_wins vs majority_vote, no-data → green-with-no_data_stations. (Pure logic, no I/O —
   easiest, highest value.)
2. `test_auth.py` — register/login/refresh happy paths + wrong password 401 + disabled user 403.
3. `test_stations_security.py` — the T01 guard: bad `station_id` → 404 (regression test for the
   injection fix).
4. `test_dedup.py` — `services/dedup.py` union-find with manual pairs.

### Step 4: CI
Add `.github/workflows/test.yml` running on `pull_request` and `push`: checkout → install Poetry →
`poetry install` → `poetry run pytest` (+ optionally `ruff check`). Make the job required to be green.

## Acceptance criteria
- `poetry run pytest` runs and passes locally with the first test files.
- Tests use in-memory SQLite + a stubbed InfluxClient; they do not require a live InfluxDB or network.
- CI workflow runs pytest on PRs and reports status.
- Document in the PR how to run the suite. (Coverage % target can grow over subsequent sessions —
  this task establishes the harness + critical-path tests, not full 80%.)

## Notes
- Frontend Playwright tests (`tests/frontend/`) are explicitly **deferred** to a later session; this
  task is backend pytest + CI only. Note the deferral in your summary.
