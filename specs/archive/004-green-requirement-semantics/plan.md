# Implementation Plan: Green Conditions Are Requirements

**Feature**: [spec.md](./spec.md)
**Phase**: 2 — Plan · **Date**: 2026-07-17
**Next step**: `tasks.md`

---

## Technical Context

| Concern | Choice |
|---|---|
| Persistence | **None.** No table, no column, no migration |
| API | **None.** No new route, no payload shape change |
| Evaluation | Add one contribution rule to `triggered_colours` in the launch/landing path |
| Frontend | Help text only (`help.html` + i18n). No behavioural JS |
| Tests | pytest, `SimpleNamespace` duck-typing per `06-testing-conventions.md` |

**Architecture approach**: the decision already flows through a single list, `triggered_colours`. A
matched unit appends its colour; the combination logic (`worst_wins` / `majority_vote`) and the
`not triggered_colours → green` default read that list. The change is one `elif`: a GREEN unit that
did not trigger appends `"red"`. Everything downstream — worst-wins, the green default, `majority_vote`
— then produces the right answer without further edits.

## Constitution Check

Per `.ai/instructions/00-ai-usage.md`.

| # | Principle | Status | Notes |
|---|---|---|---|
| 1 | Read before acting | ✅ Pass | Every claim cites file:line; existing tests read and confirmed non-breaking except one verified green-match case |
| 2 | Plan before building | ✅ Pass | This document |
| 3 | Minimal scope | ✅ Pass | Two `elif`s per block; no storage, no API. The four-way duplication is left as-is and flagged, not refactored |
| 4 | Tool-agnostic instructions | ✅ Pass | Nothing outside `.ai/` |
| 5 | Keep docs in sync | ✅ Pass | Phase 3 rewrites `help.html`, i18n, `architecture.md`, `features.md`, and the evaluator docstring |
| 6 | No secrets committed | ✅ Pass | None involved |
| 7 | Prod is off-limits | ✅ Pass | Pure logic change; ships as a normal image |

**No blocking violations.**

### Additional constraint compliance (`04-constraints.md`)

| Constraint | How this plan complies |
|---|---|
| Static assets → version bump | Phase 3 edits `help.html`, so `pyproject.toml` **must** be bumped (v1.19.0 → v1.20.0) |
| i18n | `combination_hint` and the new help copy need all four locales |
| No behavioural regression to exception rule sets | FR-003/FR-004 pinned by regression tests that assert the exception-only paths are byte-identical |

---

## The Change

For **launch/landing only** (`site_type != "opportunity"`), in each of the four decision blocks:

```python
# standalone loop — replace the bare `if matched:` append
if matched:
    triggered_colours.append(cond.result_colour)
elif ruleset.site_type != "opportunity" and cond.result_colour == "green":
    triggered_colours.append("red")          # FR-001/FR-005: unmet green requirement → red

# AND-group loop — replace the bare `if all_matched:` append
group_colour = _worst([c.result_colour for c in group_conds])
if all_matched:
    triggered_colours.append(group_colour)
elif ruleset.site_type != "opportunity" and group_colour == "green":
    triggered_colours.append("red")          # FR-001/D2: all-green group not fully met → red
```

No-data needs no branch: `_eval_condition` returns `(False, …)` for both a missing station and a
failed threshold, so both reach the `elif` (D3).

Opportunity is gated out because it already forces red when `len(triggered_colours) < total_units`;
appending red there would double-count and could flip its `< total_units` guard.

---

## File Structure

**Modify**

| File | Change |
|---|---|
| `src/lenticularis/rules/evaluator.py` | The two `elif`s in all four blocks: `_evaluate_from_station_data`, `run_evaluation`, `run_evaluation_at`, `run_forecast_evaluation`. Update the module docstring (lines 22-31) |
| `tests/backend/test_rules_evaluator.py` | New cases (FR-001, D3, group, regression); no existing case changes |
| `static/help.html` | Rewrite lines 269-271 (colour meanings), 397 (green = requirement), 410-416 (worst-wins) |
| `static/i18n/{en,de,fr,it}.json` | `combination_hint` reworded; new help keys if any are added |
| `.ai/context/architecture.md` | Rules Engine Design + benefit-of-the-doubt notes |
| `.ai/context/features.md` | v1.20.0 milestone |
| `pyproject.toml` | **Version bump v1.19.0 → v1.20.0** |
| `README.md` / `PLANNING.md` | Doc sync per `sync.md` if they describe evaluation semantics |

**Create**: none.

---

## Implementation Phases

### Phase 1 — Evaluator + tests

1. Add the two `elif`s to all four decision blocks. Verify the wording is identical in each.
2. Update the module docstring: the "benefit of the doubt → green" paragraph now applies to
   exception conditions only; GREEN conditions are requirements.
3. Tests (all via `_evaluate_from_station_data`, which backs the live/backfill path and is the core):
   - green standalone, threshold failed (data present) → red
   - green standalone, no data → red (D3)
   - green standalone, matched → green (regression)
   - all-green group, one member fails → red
   - mixed group (green+orange), not all match → green (D2: still exception)
   - red-only rule set, calm → green (FR-004 regression)
   - red-only rule set, no data → green (FR-003 regression — the `test_no_data_for_station_condition`
     invariant must hold)
   - opportunity with an unmet green → red (unchanged path, guard proof)

*Verifiable*: `poetry run pytest tests/backend/test_rules_evaluator.py -q` green; full suite green.

### Phase 2 — Docs + version

1. `help.html`: green condition is a **requirement** that must be met, else red; worst-wins section
   notes a launch/landing site is red when a required green condition is unmet; the "GREEN — all
   conditions pass" line clarified.
2. i18n `combination_hint` ×4 — mention that an unmet green requirement makes the site red.
3. `architecture.md` Rules Engine Design + the two "benefit of the doubt" mentions.
4. `features.md` → v1.20.0.
5. `pyproject.toml` bump.
6. `README.md` / `PLANNING.md` if they describe semantics.

---

## Risk & Mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **Applying the rule to opportunity sites** — double-counts and can flip the `< total_units` guard | High | Gate on `site_type != "opportunity"` in every block. Regression test on the opportunity path |
| 2 | **The four blocks diverge** — the change must be identical in each or the paths disagree (FR-007) | Medium | Apply verbatim; test the core `_evaluate_from_station_data` which backs live + backfill; add a forecast/snapshot smoke check |
| 3 | **Breaking exception-only rule sets** — the whole reason a global flip was rejected | High | The `elif` only fires for green units; exception-only sets have none. Regression tests FR-003/FR-004 |
| 4 | **Existing decisions change silently for real users** | Medium — but intended | This is the fix, not a regression. Rule sets with a GREEN condition change in the pilot's intended direction. Called out in `features.md` deploy notes |
| 5 | Help text left describing the old "benefit of the doubt" model | Medium | Phase 2 is mandatory; NFR-001 requires the one-sentence explanation to land |
| 6 | Static asset (`help.html`) changed without version bump | Medium | Phase 2 bumps `pyproject.toml` |

## Follow-up (out of scope, flagged)

**The decision block is duplicated four times** (`_evaluate_from_station_data`, `run_evaluation`,
`run_evaluation_at`, `run_forecast_evaluation`). This change touches all four. A future cleanup should
route the live/snapshot/forecast paths through the shared core so the decision logic lives once. Not
done here to keep the change surgical and reviewable.
