# Tasks: Green Conditions Are Requirements

**Feature**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md)
**Phase**: 3 — Tasks · **Date**: 2026-07-17

Ordered. Each task is independently verifiable.

## Phase 1 — Evaluator

- [ ] **T01** — `evaluator.py`: add the unmet-green→red `elif` to the **standalone** and **AND-group**
  loops in `_evaluate_from_station_data`. Gate on `site_type != "opportunity"`.
- [ ] **T02** — Repeat the identical two `elif`s in `run_evaluation`.
- [ ] **T03** — Repeat in `run_evaluation_at`.
- [ ] **T04** — Repeat in `run_forecast_evaluation`.
- [ ] **T05** — Update the `evaluator.py` module docstring (lines 22-31): benefit-of-the-doubt applies
  to exception conditions only; GREEN conditions are requirements; state the no-data fail-safe (D3).

## Phase 2 — Tests

- [ ] **T06** — `test_rules_evaluator.py`: green standalone, data present, threshold failed → red.
- [ ] **T07** — green standalone, no data → red (D3).
- [ ] **T08** — all-green AND group, one member fails → red.
- [ ] **T09** — mixed group (green+orange), not all match → green (D2 regression).
- [ ] **T10** — red-only rule set, calm → green; red-only, no data → green (FR-003/FR-004 regression).
- [ ] **T11** — opportunity with an unmet green → red (guard proof, unchanged path).
- [ ] **T12** — run `poetry run pytest -q` and `ruff check`; whole suite green.

## Phase 3 — Docs + version

- [ ] **T13** — `help.html`: rewrite green = requirement (line 397), colour meanings (269-271),
  worst-wins section (410-416).
- [ ] **T14** — i18n `combination_hint` ×4 (en/de/fr/it) + any new help keys.
- [ ] **T15** — `.ai/context/architecture.md` Rules Engine Design + benefit-of-the-doubt notes.
- [ ] **T16** — `.ai/context/features.md` → v1.20.0 milestone with deploy note (decisions change for
  rule sets containing GREEN conditions).
- [ ] **T17** — `pyproject.toml` version → 1.20.0.
- [ ] **T18** — `README.md` / `PLANNING.md` sync if they describe evaluation semantics.
