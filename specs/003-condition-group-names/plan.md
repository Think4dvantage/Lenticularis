# Implementation Plan: Named Condition Groups

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md) · **Data model**: [data-model.md](./data-model.md) · **Contracts**: [contracts/condition-groups.yaml](./contracts/condition-groups.yaml)
**Phase**: 2 — Plan · **Date**: 2026-07-16
**Next step**: `tasks.md`

---

## Technical Context

| Concern | Choice |
|---|---|
| Persistence | New `condition_groups` table via `Base.metadata.create_all()`; guarded backfill in `_run_column_migrations()`. No Alembic |
| Integrity | ORM `cascade="all, delete-orphan"` — SQLite FKs are **not** enforced here (no `PRAGMA foreign_keys=ON`, `db.py:97-99`) |
| Evaluation | **Unchanged.** Groups stay derived from conditions; only `ConditionResult` gains `group_name` |
| API | No new routes. Two existing payloads extended |
| Frontend | Vanilla JS in `static/ruleset-editor.html`. No build step |
| Tests | pytest, `SimpleNamespace` duck-typing for evaluator tests per `06-testing-conventions.md` |

**Architecture approach**: make groups real in storage and presentation while leaving the decision
path untouched. The feature's whole risk profile hinges on that separation (R2).

**Key dependencies**: none new.

---

## Constitution Check

Per `.ai/instructions/00-ai-usage.md`.

| # | Principle | Status | Notes |
|---|---|---|---|
| 1 | Read before acting | ✅ Pass | Every design claim cites file:line; one earlier claim was checked and **retracted** (see Risk 1) |
| 2 | Plan before building | ✅ Pass | This document. No code written |
| 3 | Minimal scope | ⚠️ **Justified exception** | "Add a name" became a new table + migration + save-path change. This follows from **D1**, an explicit user decision taken with the smaller alternative offered and declined. The smaller option could not satisfy the actual requirement (a name that survives on a one-condition group) |
| 4 | Tool-agnostic instructions | ✅ Pass | Nothing outside `.ai/` |
| 5 | Keep docs in sync | ✅ Pass | Phase 5. `architecture.md`'s schema section and its "condition_groups does not exist" note **must** be corrected — it was rewritten this session and this feature falsifies it |
| 6 | No secrets committed | ✅ Pass | None involved |
| 7 | Prod is off-limits | ✅ Pass | Migration runs at container startup like every other |

**No blocking violations.**

### Additional constraint compliance (`04-constraints.md`)

| Constraint | How this plan complies |
|---|---|
| No Alembic / no `.sql` | `create_all()` for the table; guarded backfill in `_run_column_migrations()` |
| T18 — no swallowed exceptions | The backfill must not be wrapped in a bare `except: pass`. If it fails, fail loudly at startup |
| T12 — error envelope | Fail-closed validation returns `VALIDATION_FAILED` through the standard envelope |
| T03 — XSS | **Group names are untrusted user input rendered in the editor and (later) the popup.** `textContent` only, never `innerHTML` |
| i18n | New editor strings need keys in all four locales simultaneously |
| Static assets → version bump | Phase 4 touches `static/`, so `pyproject.toml` **must** be bumped |

---

## Data Model Summary

New `condition_groups` table (`id`, `ruleset_id`, `name` nullable, `sort_order`) — the table
`models.py:253` already predicted. `rule_conditions.group_id` **is not altered**; it simply gains a
parent. Backfill inserts one unnamed group per distinct existing `group_id`, **reusing the existing
id** so no condition row is touched and decisions provably cannot move. Full detail in
[data-model.md](./data-model.md).

---

## File Structure

**Create**

| File | Purpose |
|---|---|
| `tests/backend/test_condition_groups.py` | Migration, backfill, empty-group inertness, clone remap, fail-closed validation |

**Modify**

| File | Change |
|---|---|
| `src/lenticularis/database/models.py` | `ConditionGroup` model; `RuleSet.condition_groups` relationship with cascade; update the `group_id` comment on line 253 that predicts this table |
| `src/lenticularis/database/db.py` | Guarded backfill in `_run_column_migrations()` |
| `src/lenticularis/models/rules.py` | `ConditionGroupIn/Out`; `groups` on `ConditionsReplaceRequest` + validator; `condition_groups` on `RuleSetDetail`; `group_name` on `ConditionResult` |
| `src/lenticularis/api/routers/rulesets.py` | `replace_conditions` replaces groups too; **`clone_ruleset` remaps group ids (R5)** |
| `src/lenticularis/rules/evaluator.py` | **Output only** — populate `group_name`. Decision logic untouched |
| `static/ruleset-editor.html` | Name input per group; send `groups`; rebuild from `condition_groups`; remove the `rows.length > 1` collapse |
| `static/i18n/{en,de,fr,it}.json` | Group-name keys — all four |
| `pyproject.toml` | **Version bump** |
| `.ai/context/architecture.md`, `.ai/context/features.md` | Doc sync — schema section is now wrong |

---

## Implementation Phases

### Phase 1 — Storage and migration (no user-visible change)

1. `ConditionGroup` model + `RuleSet.condition_groups` relationship with
   `cascade="all, delete-orphan"` (**load-bearing** — SQLite will not cascade for us, R6).
2. Guarded backfill in `_run_column_migrations()`, reusing existing `group_id` values as row ids.
3. Tests: backfill creates exactly one unnamed group per distinct `group_id`; is idempotent;
   **decisions before and after the migration are identical**.

*Verifiable*: boot against a copy of a real DB; grouping and decisions unchanged.

### Phase 2 — API

1. `ConditionGroupIn/Out`; `groups` on `ConditionsReplaceRequest`; `condition_groups` on
   `RuleSetDetail`.
2. **Fail-closed validator**: every non-null `condition.group_id` must appear in `groups`, else 422
   (R3). This is what stops a caller silently wiping every group name.
3. `replace_conditions` replaces groups and conditions atomically.
4. **`clone_ruleset` remap (R5)** — create new group rows for the clone, build `{old: new}`, remap
   `group_id`. Without this the clone points at the source's groups.
5. `group_name` on `ConditionResult`, populated by lookup in the evaluator's **output** path only.
6. Tests: round-trip; 422 on dangling `group_id`; clone produces independent groups; renaming a
   source group does **not** rename the clone's.

### Phase 3 — Evaluator output enrichment

1. Build `{group_id: name}` from the rule set; populate `group_name` on grouped results.
2. **Do not touch the decision logic.** Do not iterate group rows (R2).
3. Tests: an empty group changes nothing; a one-condition named group evaluates identically to a
   standalone condition; `group_name` is `None` for standalone and unnamed.

### Phase 4 — Editor

1. Name input per group; send `groups` on save.
2. Rebuild groups from `condition_groups` rather than inferring from `group_id` collisions
   (`ruleset-editor.html:1125-1129`).
3. **Remove `group_id: rows.length > 1 ? gid : null`** (`ruleset-editor.html:988`) — the collapse
   defect. Groups now persist at any size.
4. Render empty groups as empty containers (FR-011).
5. Deleting a group tells the pilot what happens to its conditions first (FR-013).
6. `textContent` only for names (T03). `[Lenti:editor]` logging on every new path.
7. i18n ×4. **Bump `pyproject.toml`.**

### Phase 5 — Docs sync

`architecture.md` — the schema section says `condition_groups` **never existed**; that becomes false
the moment Phase 1 ships. The "Rules Engine Design" note that grouping is `group_id`-only also needs
revising. `features.md` gains a milestone. Constitution #5.

---

## Dependencies

- **External**: none.
- **Internal**: `_evaluate_from_station_data()` (read, not changed), `_run_column_migrations()`,
  the `RuleSet.conditions` relationship as the cascade template.
- **Not dependent on `002`**, and `002` does not depend on this. Ship in either order.
- **The tooltip change consumes this.** Sequence it after Phase 3, when `group_name` exists.

---

## Risk & Mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **Refactoring the evaluator to iterate group rows** — the natural move once groups are first-class. Opens `_worst([])` → `ValueError` on an empty group, killing evaluation for the whole rule set, and inflates `total_units` so opportunity sites go permanently red | **High** | R2: keep deriving groups from conditions. FR-011/FR-012 then hold for free and NFR-001 is true by construction. Regression test pins it. *(An earlier revision of the checklist wrongly called this crash reachable today — it is not, precisely because the evaluator derives from conditions. The correction is recorded there.)* |
| 2 | **Clone shares the source's groups** — `rulesets.py:597` copies `group_id` verbatim. Renaming the source renames the copy; deleting the source orphans it | **High** — silent, breaks FR-008 | R5: remap through `{old: new}`. Test that renaming a source group leaves the clone untouched. The line looks correct today, which is why it needs a test and not just care |
| 3 | **A caller omitting `groups` wipes every group name** | **High** — silent data loss | R3: fail-closed 422 rather than a permissive default |
| 4 | **Migration moves someone's decisions** | **High** — silently changes safety calls | Backfill reuses existing `group_id`s as row ids, so no condition row changes and the evaluator's buckets are identical. Test decisions across the migration |
| 5 | Groups orphaned on rule set delete — SQLite will not cascade (no `PRAGMA foreign_keys=ON`) | Medium | ORM `cascade="all, delete-orphan"`, mirroring `RuleSet.conditions`. Test deleting a rule set removes its groups |
| 6 | XSS via a group name in the editor or popup | Medium | `textContent` only (T03). Names are untrusted free text |
| 7 | Static assets change without a version bump | Medium | Phase 4 bumps `pyproject.toml` |
| 8 | `architecture.md` now actively lies about the schema | Low | Phase 5 is mandatory, not optional |

## Pre-existing defect found during planning — out of scope, needs its own decision

**`clone_ruleset` does not copy `site_type` (`rulesets.py:570-582`).**

The `RuleSet(...)` constructor there sets `id`, `owner_id`, `name`, `description`, `lat`, `lon`,
`altitude_m`, `combination_logic`, `is_public`, `clone_count`, `cloned_from_id` — and **not**
`site_type`. The column defaults to `"launch"` (`models.py:121`), so **cloning a landing zone or an
opportunity site silently produces a launch site.**

Unrelated to this feature and deliberately not fixed here (Constitution #3). Flagged because it is a
real, silent data defect: the clone evaluates under launch rules, and for an opportunity site that is
a materially different code path (`evaluator.py:253-255` requires *every* unit to trigger, or it goes
red). Worth its own small fix.
