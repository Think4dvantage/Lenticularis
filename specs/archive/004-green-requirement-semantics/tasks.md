# Tasks: Green Conditions Are Requirements

**Feature**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)
**Phase**: 3 — Tasks · **Date**: 2026-07-17
**Status**: Complete — shipped as v1.20.0 (commit `092ed04`, tag `v1.20.0`).

Ordered. Each task is independently verifiable.

## Phase 1 — Evaluator

- [x] **T01** — `evaluator.py`: add the unmet-green→red `elif` to the **standalone** and **AND-group**
  loops in `_evaluate_from_station_data`. Gate on `site_type != "opportunity"`.
- [x] **T02** — Repeat the identical two `elif`s in `run_evaluation`.
- [x] **T03** — Repeat in `run_evaluation_at`.
- [x] **T04** — Repeat in `run_forecast_evaluation`.
- [x] **T05** — Update the `evaluator.py` module docstring (lines 22-31): benefit-of-the-doubt applies
  to exception conditions only; GREEN conditions are requirements; state the no-data fail-safe (D3).

## Phase 2 — Tests

- [x] **T06** — `test_rules_evaluator.py`: green standalone, data present, threshold failed → red.
- [x] **T07** — green standalone, no data → red (D3).
- [x] **T08** — all-green AND group, one member fails → red.
- [x] **T09** — mixed group (green+orange), not all match → green (D2 regression).
- [x] **T10** — red-only rule set, calm → green; red-only, no data → green (FR-003/FR-004 regression).
- [x] **T11** — opportunity with an unmet green → red (guard proof, unchanged path).
- [x] **T12** — run `poetry run pytest -q` and `ruff check`; whole suite green.

## Phase 3 — Docs + version

- [x] **T13** — `help.html`: rewrite green = requirement (line 397), colour meanings (269-271),
  worst-wins section (410-416).
- [x] **T14** — i18n `combination_hint` ×4 (en/de/fr/it) + any new help keys.
- [x] **T15** — `.ai/context/architecture.md` Rules Engine Design + benefit-of-the-doubt notes.
- [x] **T16** — `.ai/context/features.md` → v1.20.0 milestone with deploy note (decisions change for
  rule sets containing GREEN conditions).
- [x] **T17** — `pyproject.toml` version → 1.20.0.
- [x] **T18** — `README.md` / `PLANNING.md` sync if they describe evaluation semantics.
