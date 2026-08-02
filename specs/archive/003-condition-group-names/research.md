# Research: Named Condition Groups

**Feature**: [spec.md](./spec.md)
**Phase**: 0 — Research
**Date**: 2026-07-16

Unknowns identified in the spec, resolved here before design.

---

## R1 — How do first-class groups get stored? (D1, FR-010)

- **Decision**: A new `condition_groups` table. `rule_conditions.group_id` stays a plain string
  column and becomes a reference to it.
- **Rationale**: D1 requires a group to survive with one condition or none, which is impossible while
  a group is merely an emergent property of the conditions inside it. The name must live in exactly
  one place, or renaming becomes an update across every member row. `database/models.py:253` already
  anticipates this table by name ("future: group_id references a condition_groups table"), so this is
  the design the codebase was already pointing at.
- **Alternatives considered**:
  - *`group_name` column on `rule_conditions`* — no new table, but denormalised: the name repeats on
    every member, renaming is a multi-row update, and a group with zero members has nowhere to exist.
    Fails D1 outright.
  - *JSON blob of group metadata on `rulesets`* — avoids a table but makes groups unqueryable and
    unjoinable, and invents a second storage idiom for no benefit.

## R2 — Does the evaluator change? (NFR-001, FR-011, FR-012)

- **Decision**: **The decision logic is not modified at all.** The evaluator keeps deriving its group
  buckets from conditions (`evaluator.py:201-205`). The only change is enriching the *output* with
  `group_name`. Do **not** refactor it to iterate group rows.
- **Rationale**: This is the single most important decision in the feature, and it is
  counter-intuitive. Once groups are first-class, iterating `ruleset.condition_groups` looks like the
  natural implementation. It is a trap:
  - Deriving from conditions makes **FR-011 free** — a group with no conditions contributes no
    conditions, so it never becomes a bucket key, never counts toward `total_units`
    (`evaluator.py:252`), and is inert by construction rather than by a guard someone must remember.
  - It makes **FR-012 free** for the same reason — a one-condition group already evaluates identically
    to a standalone condition (verified: `total_units` is unchanged by which bucket a condition lands
    in, and a one-member group contributes `_worst([single])`, which is that colour).
  - It makes **NFR-001 true by construction**. If the decision code is untouched, decisions cannot
    shift. Any other approach requires *proving* equivalence instead of getting it for free.
  - Iterating group rows would reach `_worst([])` → `ValueError` on an empty group, taking out the
    whole rule set's evaluation (see the checklist's hazard section for the full trace).
- **Consequence**: groups are a **storage and presentation** concern. Evaluation stays
  condition-driven. `group_name` is added to `ConditionResult` and populated from a `{group_id: name}`
  map built off the rule set — a pure lookup that cannot affect any outcome.
- **Alternatives considered**:
  - *Iterate group rows* — "cleaner" in the object-model sense, actively worse in every other: opens
    the crash, requires an explicit inertness guard, and puts NFR-001 at risk for no gain.

## R3 — How do groups survive wholesale condition replacement?

- **Decision**: Extend `ConditionsReplaceRequest` with a `groups` list and replace both together in
  one request. Reject any payload where a condition references a `group_id` not present in `groups`.
- **Rationale**: Conditions are saved by full replacement — `replace_conditions` deletes every
  condition and re-inserts it (`rulesets.py:393-403`), **regenerating each `id` server-side**. Groups
  cannot be inferred from the conditions, because FR-011 permits a group with zero conditions, which
  would leave no trace. So the client must send groups explicitly, and the save must be atomic:
  replacing conditions without replacing groups would strand references.
  Fail-closed validation matters here — if `groups` were merely optional and defaulted to empty, an
  older client (or any caller that omits the field) would **silently delete every group name** the
  pilot had typed. Rejecting the payload turns silent data loss into a loud 422.
- **Alternatives considered**:
  - *Separate group CRUD routes* — more REST-shaped, but the editor saves atomically; two requests
    introduce partial states (conditions saved, groups not) with no transaction spanning them.
  - *Auto-create missing groups server-side from condition `group_id`s* — hides client bugs and
    cannot represent an empty group.

## R4 — What happens to existing rule sets? (FR-005, NFR-001, NFR-002)

- **Decision**: Backfill. For every distinct non-NULL `group_id` currently in `rule_conditions`,
  insert a `condition_groups` row with that exact id, its rule set, and `name = NULL`.
- **Rationale**: Preserves grouping byte-for-byte — conditions keep the `group_id` values they
  already carry, so the evaluator's buckets are identical and decisions cannot move (NFR-001). Legacy
  groups then exist as real rows and simply appear unnamed (FR-005), which is exactly the specified
  behaviour. Nothing is invented and nothing is lost (NFR-002).
- **Note**: the table itself is created by `Base.metadata.create_all()`, which is idempotent and
  handles new tables at startup (`02-backend-conventions.md`). Only the **backfill** needs a guard in
  `_run_column_migrations()`, and it must be safe to re-run.
- **Alternatives considered**:
  - *Leave legacy `group_id`s dangling* — grouping still evaluates (the evaluator only buckets by
    value), but the groups would be invisible and unnameable in the editor, which fails FR-005.
  - *Discard legacy grouping* — silently changes people's decisions. Violates NFR-001/NFR-002.

## R5 — Cloning must remap group references (FR-008)

- **Decision**: `clone_ruleset` creates fresh `condition_groups` rows for the clone and remaps each
  condition's `group_id` through an `{old_id: new_id}` map.
- **Rationale**: **This is a live bug the moment groups become real.** `clone_ruleset` currently
  copies `group_id=c.group_id` verbatim (`rulesets.py:597`). Today that is harmless — `group_id` is
  an opaque string with no parent, so the clone merely reuses the same marker and grouping is
  preserved. Once groups are rows owned by a `ruleset_id`, the clone's conditions would point at the
  **source's** groups: renaming the source's group would rename the copy's (breaking FR-008's
  requirement that the copy be independent), and deleting the source would orphan them. The line
  looks correct and already-working, which is precisely why it would be missed.
- **Alternatives considered**: none — sharing group rows across rule sets is simply wrong.

## R6 — Referential integrity and cascade

- **Decision**: Rely on SQLAlchemy ORM cascade, exactly as `RuleSet.conditions` already does. Declare
  the relationship with `cascade="all, delete-orphan"`.
- **Rationale**: **SQLite foreign keys are not enforced in this deployment.** `db.py:97-99` sets only
  `journal_mode`, `synchronous` and `busy_timeout` — there is no `PRAGMA foreign_keys=ON`, so every
  `ondelete="CASCADE"` in `models.py` is declarative documentation, not enforcement. Deleting a rule
  set today removes its conditions because of the ORM relationship's `cascade="all, delete-orphan"`
  (`models.py:154-157`), not because of the database. Groups must follow the same pattern or they
  will be orphaned on rule set deletion.
- **Consequence**: do **not** assume the database will clean up after a group delete. FR-013 (deleting
  a group must not silently orphan its conditions) has to be handled in application code.
- **Alternatives considered**:
  - *Enable `PRAGMA foreign_keys=ON`* — arguably correct, but it would begin enforcing every
    previously-declarative FK across the whole schema at once. That is a repo-wide behavioural change
    with unknown blast radius, and is far outside this feature. Worth raising separately.
