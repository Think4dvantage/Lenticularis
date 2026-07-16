# Specification Quality Checklist: Named Condition Groups

**Created**: 2026-07-16
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] All mandatory sections completed

## Requirement Completeness

- [x] **No [NEEDS CLARIFICATION] markers remain** — Q1 resolved 2026-07-16 and recorded as D1
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable and technology-agnostic
- [x] All acceptance scenarios are defined
- [x] Edge cases are identified
- [x] Dependencies and assumptions identified

## Feature Readiness

- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] No implementation details leak into specification

## Verdict

**Ready for `plan.md`.** 12/12. D1 chose first-class groups, which resolved FR-010 and forced three
follow-on requirements into the open (FR-011 empty groups inert, FR-012 one-condition equivalence,
FR-013 deletion is explicit).

Scope grew during clarification, honestly: this is no longer "add a name field". It is a new stored
entity, a migration of every existing rule set's grouping, and a change to how conditions are saved.
The payoff is that it repairs the collapse defect below as a by-product rather than needing a
separate fix, and it is what makes a readable tooltip possible at all.

Everything else was guessable and guessed — optional names, no uniqueness constraint, plain text,
existing permissions, no invented "Group 1" labels, names travel with copies.

## Pre-existing defect surfaced during specification

Independent of this feature, and worth deciding on regardless:

- A pilot who creates a condition group and then deletes all but one of its conditions **silently
  loses the grouping on save**, with no warning. The group identity is discarded whenever a group
  holds fewer than two conditions (`static/ruleset-editor.html:988`). Nothing tells the pilot this
  happened.

  **This is decision-neutral today — verified, not assumed.** `total_units` is
  `len(standalone) + len(groups)` (`rules/evaluator.py:252`), so moving a condition from a one-member
  group into the standalone bucket leaves the unit count unchanged. The group path contributes
  `_worst([single_colour])`, which is that colour exactly (`evaluator.py:250`), identical to what the
  standalone path contributes (`evaluator.py:227`). All three decision paths — opportunity,
  `worst_wins`, `majority_vote` — read only `triggered_colours` and `total_units`
  (`evaluator.py:253-271`). The outcome is therefore provably the same, which is precisely why this
  has gone unnoticed for so long.

  It stops being invisible the moment groups carry names: the pilot loses a *name* they typed, not
  just an internal marker. Q1 decides whether this is preserved or fixed.

## ⚠ Latent hazard that D1 makes reachable — must be designed around in `plan.md`

> **Correction (2026-07-16).** An earlier revision of this checklist stated that an empty group
> "crashes the evaluator today" and that the path was reachable as soon as groups could be empty.
> **That was wrong**, and is corrected below. The hazard is real but strictly conditional on *how*
> the feature is implemented. The distinction matters: it turns a mandatory guard into a design
> constraint, and it is the difference between changing the evaluator and not touching it at all.

**The crash is currently unreachable.** The evaluator builds its group buckets from *conditions*:

```python
for c in conditions:                                  # evaluator.py:201-205
    if c.group_id:
        groups.setdefault(c.group_id, []).append(c)   # always appends ≥ 1
```

Every value in `groups` therefore holds at least one condition, `evals` is never `[]`, and the
`_worst([])` branch cannot be entered. A zero-condition group never becomes a key at all — it is
simply invisible to the evaluator.

**It becomes reachable if the evaluator is changed to iterate group rows** — which is the natural,
tempting refactor once groups are first-class entities (`for g in ruleset.condition_groups: ...`).
That version would produce:

```
group_conds = []                                  # an empty group row
evals       = []
all_matched = all([])            → True           # vacuous truth, evaluator.py:233
if all_matched:                  → entered        # evaluator.py:249
    _worst([])                   → ValueError     # evaluator.py:250, max() of empty sequence
```

`_worst()` is `max(colours, key=...)` (`evaluator.py:94-96`) and raises on an empty sequence. Per
`04-constraints.md` (T18, no swallowed exceptions) that propagates and takes out the entire
evaluation for that rule set — and live evaluation runs for every rule set on the map and in the
scheduler, so one empty group would break a pilot's whole map. `total_units = len(standalone) +
len(groups)` (`evaluator.py:252`) would also count the empty group as a unit, turning every
opportunity site containing one permanently red (`evaluator.py:253-255` requires every unit to
trigger).

**Design constraint for `plan.md`: keep the evaluator deriving groups from conditions. Do not
iterate group rows.** Doing so satisfies FR-011 (empty groups inert) *for free* — an empty group
contributes no conditions, hence no unit and no colour — and guarantees NFR-001 (identical decisions)
by construction, because the decision logic is then not modified at all.

Still add a regression test asserting that a rule set containing an empty group evaluates identically
to one without it. The test is cheap, and it pins the constraint so a future refactor toward group-row
iteration fails loudly instead of silently shipping the crash.

## Notes carried forward to `plan.md` (not spec concerns)

Implementation constraints found during recon. Deliberately absent from the spec per `specify.md`
("never HOW"), and must not be lost:

- There is **no `condition_groups` table**. Grouping is `rule_conditions.group_id` — an opaque string
  with no parent entity, no name, no ordering, and no constraint. `database/models.py:253` explicitly
  anticipates a real table as future work. Q1 = "labelled collection" means a name column on the
  condition rows (denormalised, repeated per member, with update anomalies). Q1 = "first-class"
  means the table that comment predicted.
- `group_id` is **minted client-side** (`static/ruleset-editor.html:758`) and sent up with the
  conditions. The server never generates it and never validates it.
- `static/ruleset-editor.html:988` sends `group_id: rows.length > 1 ? gid : null` — this single
  expression is the source of the collapse defect above.
- Groups are **reconstructed on load** by bucketing conditions that share a `group_id`
  (`static/ruleset-editor.html:1125-1129`), so group ordering is currently incidental, not stored.
- Conditions are saved by **wholesale replacement** (`ConditionsReplaceRequest`), not incremental
  edit — so any group entity must survive a full condition replace, or be replaced alongside it.
  This is the single most important constraint on the data model if Q1 = "first-class".
- The evaluation payload **already carries `group_id` and `group_all_matched`** per condition
  (`models/rules.py:165-166`), so consumers already receive group structure — they simply have no
  name to show. Adding the name to that payload is what FR-007 requires, and is what the separate
  tooltip change will consume.
- Schema changes here follow `02-backend-conventions.md`: `Base.metadata.create_all()` for a new
  table, or a guarded `ALTER TABLE` in `_run_column_migrations()` for a new column. **No Alembic.**
- NFR-001/NFR-002 make this a migration with a correctness bar: existing groupings must survive
  intact and decisions must not shift. The evaluator's grouping logic (`rules/evaluator.py:202-203`,
  `340-341`) reads `group_id` off conditions and must keep working throughout.
