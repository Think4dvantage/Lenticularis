# Tasks: Named Condition Groups

**Spec**: [spec.md](./spec.md) · **Plan**: [plan.md](./plan.md) · **Research**: [research.md](./research.md) · **Data model**: [data-model.md](./data-model.md) · **Contracts**: [contracts/condition-groups.yaml](./contracts/condition-groups.yaml)
**Next step**: `analyze.md` → `implement.md`

## Summary

- **Total tasks**: 21
- **Parallel opportunities**: 7 marked `[P]`
- **MVP scope**: Phase 2 + Phase 3 (US1 only) — a pilot can name groups and they persist. US2–US3 are additive.
- **Test tasks included**, for the same reason as `002`: `06-testing-conventions.md` requires backend
  logic to be test-gated, and this feature's risks are *silent* — a migration that moves someone's
  decisions, a save that wipes typed names, a clone that shares its source's groups. None of those
  announce themselves.

## Dependencies

```
Phase 1 — Setup            (none)
        │
Phase 2 — Foundation       T001–T005  ← blocks everything
        │                  model + cascade → backfill migration → prove decisions unchanged
        │
        ├── Phase 3 — US1  T006–T014  name + persist + edit      ← MVP ends here
        │                             (API replace path, editor, collapse-defect removal)
        │
        ├── Phase 4 — US2  T015–T016  group_name in decisions    (needs T006)
        │
        └── Phase 5 — US3  T017–T018  names survive cloning      (needs T001)
                    │
Final  — Polish            T019–T021  docs + version bump
```

---

## Phase 1 — Setup

No tasks. No new dependencies. The table is created by `Base.metadata.create_all()`, which is
idempotent and already runs at startup (`02-backend-conventions.md`) — only the **backfill** needs
writing.

---

## Phase 2 — Foundation

**Goal**: groups exist as real rows, existing rule sets are migrated, and decisions provably have not
moved. No user-visible change.
**Blocks**: all stories.

- [ ] T001 Add the `ConditionGroup` model to `src/lenticularis/database/models.py`: `id` (String PK), `ruleset_id` (FK → `rulesets.id`, `ondelete="CASCADE"`, indexed), `name` (String, **nullable** — NULL means never named), `sort_order` (Integer, default 0). This is the table the comment at `models.py:253` has been predicting
- [ ] T002 Add the `RuleSet.condition_groups` relationship in `src/lenticularis/database/models.py` with `cascade="all, delete-orphan"` and `order_by="ConditionGroup.sort_order"`, mirroring `RuleSet.conditions` at `models.py:154-157`. **The cascade is load-bearing, not decorative**: SQLite enforces no foreign keys here (`db.py:97-99` sets no `PRAGMA foreign_keys=ON`), so `ondelete="CASCADE"` is documentation only and the ORM does the real work (research R6). Without it, groups outlive their rule set forever
- [ ] T003 Update the stale comment at `src/lenticularis/database/models.py:253` — it says a `condition_groups` table is "future"; it is now present
- [ ] T004 Add the guarded backfill to `_run_column_migrations()` in `src/lenticularis/database/db.py`: for every distinct non-NULL `(ruleset_id, group_id)` in `rule_conditions`, insert a `condition_groups` row **reusing the existing `group_id` as the row id**, with `name = NULL`. Guard on `SELECT COUNT(*) FROM condition_groups == 0` so it is safe to re-run. Reusing the id means **no condition row is touched**, so the evaluator's buckets are byte-for-byte identical (research R4). Do not wrap in a bare `except` — fail loudly (constraint T18)
- [ ] T005 [P] Tests in `tests/backend/test_condition_groups.py`: backfill creates exactly one unnamed group per distinct `group_id`; is idempotent across two runs; **decisions are identical before and after the migration** (NFR-001 — build a rule set with groups, evaluate, migrate, evaluate again, assert equal); deleting a rule set removes its groups (T002's cascade)

---

## Phase 3 — US1: An owner remembers their own intent [US1]

**Goal**: a pilot can name each group, and the name survives saving and reloading.
**Independent test criteria**: name three groups, save, reload — names are still there. Reduce a
named group to one condition, save, reload — the group and its name still exist.

- [ ] T006 [P] [US1] Add `ConditionGroupIn` / `ConditionGroupOut` (id, name, sort_order) to `src/lenticularis/models/rules.py`; add `condition_groups: list[ConditionGroupOut]` to `RuleSetDetail`
- [ ] T007 [US1] Add `groups: list[ConditionGroupIn]` to `ConditionsReplaceRequest` in `src/lenticularis/models/rules.py` with a **fail-closed validator**: every non-null `condition.group_id` must appear in `groups`, else `VALIDATION_FAILED` (422). Without this, any caller omitting `groups` **silently deletes every group name the pilot typed** — a 422 is vastly preferable to silent data loss (research R3)
- [ ] T008 [US1] Extend `replace_conditions` in `src/lenticularis/api/routers/rulesets.py:383` to replace groups and conditions **atomically** in one transaction. Groups cannot be inferred from conditions, because a group may legitimately hold zero (FR-011) and would leave no trace
- [ ] T009 [P] [US1] Tests in `tests/backend/test_condition_groups.py`: group round-trips through save/reload with its name; a dangling `condition.group_id` → 422 and **nothing is written**; a group with zero conditions round-trips and persists
- [ ] T010 [US1] Add a name input per group in `static/ruleset-editor.html` and send `groups` on save. Use `textContent` when rendering names — they are untrusted free text (constraint T03). Add `[Lenti:editor]` logging to new paths
- [ ] T011 [US1] **Remove the collapse defect** — delete `group_id: rows.length > 1 ? gid : null` at `static/ruleset-editor.html:988` and always send the group id. This single expression is why a group holding one condition is silently discarded on save today
- [ ] T012 [US1] Rebuild groups from `condition_groups` in `static/ruleset-editor.html:1125-1129` rather than inferring them by bucketing shared `group_id`s. Render an empty group as an empty container (FR-011) — it must be visible and nameable, not hidden
- [ ] T013 [US1] Make group deletion explicit in `static/ruleset-editor.html`: tell the pilot what happens to the conditions inside before it happens (FR-013). Never silently destroy or orphan them
- [ ] T014 [P] [US1] Add group-name i18n keys to **all four** of `static/i18n/{en,de,fr,it}.json`

---

## Phase 4 — US2: A decision can be explained in words [US2]

**Goal**: the group's name is available to whatever presents a decision.
**Independent test criteria**: evaluate a rule set with a named group; the result carries the name.

- [ ] T015 [US2] Add `group_name: Optional[str]` to `ConditionResult` in `src/lenticularis/models/rules.py`, and populate it in `src/lenticularis/rules/evaluator.py` from a `{group_id: name}` map built off the rule set. **Output enrichment only — do not touch the decision logic, and do not iterate group rows.** Keep the group buckets derived from conditions (`evaluator.py:201-205`). Doing so keeps FR-011/FR-012 true for free and makes NFR-001 true by construction; iterating group rows instead reaches `_worst([])` → `ValueError` on an empty group and kills the whole rule set's evaluation (research R2)
- [ ] T016 [P] [US2] Tests in `tests/backend/test_condition_groups.py`: **a rule set with an empty group evaluates identically to one without it** (pins the R2 constraint so a future refactor toward group-row iteration fails loudly); a one-condition named group evaluates identically to a standalone condition (FR-012); `group_name` is `None` for standalone and for unnamed groups

---

## Phase 5 — US3: Names travel with a copy [US3]

**Goal**: cloning a rule set copies its group names, independently.
**Independent test criteria**: clone a rule set with named groups; rename the source's group; the
clone's name is unchanged.

- [ ] T017 [US3] Fix `clone_ruleset` in `src/lenticularis/api/routers/rulesets.py:557-604`: create **new** `condition_groups` rows for the clone, build an `{old_id: new_id}` map, and remap each condition's `group_id` through it. Line 597 currently copies `group_id=c.group_id` verbatim — harmless while `group_id` is an opaque marker, but once groups are rows owned by a `ruleset_id` the clone would point at the **source's** groups: renaming the source renames the copy, and deleting the source orphans it (research R5)
- [ ] T018 [P] [US3] Tests in `tests/backend/test_condition_groups.py`: a clone gets its own group rows; **renaming a source group leaves the clone's name unchanged** (FR-008); deleting the source rule set leaves the clone's groups intact

---

## Final Phase — Polish

- [ ] T019 [P] Correct `.ai/context/architecture.md`: the SQLite section currently states `condition_groups` **never existed** and that grouping is `group_id`-only — both become false with T001. Update the table list and the "Rules Engine Design" section. (That section was rewritten this session to fix the *opposite* error; it now needs updating for the same reason it needed fixing — the docs must track the code)
- [ ] T020 [P] Add the milestone to `.ai/context/features.md`, noting that the one-condition collapse defect is fixed as a by-product
- [ ] T021 **Bump the version in `pyproject.toml`.** Phase 3 changes `static/`, and the version *is* the cache key — without a bump, changed assets stay pinned in browsers for a year

---

## Notes

**The four tasks that carry the real risk**, if attention is scarce:

- **T004** — the backfill. Get it wrong and you silently change people's safety decisions.
- **T007** — fail-closed validation. Get it wrong and a save silently wipes every name typed.
- **T015** — evaluator enrichment. Refactor it "properly" to iterate group rows and you ship a crash.
- **T017** — the clone remap. The line it fixes looks correct today, which is exactly why it needs a test.

**Sequencing with the tooltip.** The separate tooltip change consumes `group_name` and should follow
T015. Building it earlier means building it twice.
