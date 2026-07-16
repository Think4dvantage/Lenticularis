# Feature: Named Condition Groups

**Created**: 2026-07-16
**Status**: Clarified — 0 open questions, ready for `plan.md`
**Next step**: `plan.md`

## Overview

A condition group lets a pilot require several conditions to hold together — for example, three
stations that must *all* show föhn signs before the group counts. The group is the unit in which a
pilot actually thinks: it represents one risk.

Today that meaning is invisible. A group appears as an unlabelled box, and nothing records what risk
it guards against. A pilot returning to their own rule set months later has to re-read every
condition and reconstruct their own intent. This feature lets each group carry a name.

The name is a memory aid for the owner first. It also gives every other part of the product a short,
human way to say *why* a site is red — something that currently can only be expressed by listing raw
numbers.

## User Stories

### P1 — An owner remembers their own intent

As a pilot, I want to name each condition group, so that when I come back to a rule set I immediately
recognise which risk that group covers without re-reading its conditions.

**Acceptance Criteria**:
- Each condition group can be given a name while editing a rule set
- The name is shown wherever that group is shown while editing
- The name survives saving and reloading the rule set
- Naming a group never changes the decision that rule set produces
- A group that has no name continues to work exactly as it does today

### P2 — A decision can be explained in words

As a pilot, I want the name of the group responsible for a decision to be available, so that a red
site can be explained as "Föhn risk" rather than as a list of numbers.

**Acceptance Criteria**:
- When a group determines an outcome, its name is available to whatever presents that outcome
- A group with no name falls back to today's behaviour and is still presentable
- This feature makes the name *available*; it does not itself redesign any display

### P3 — Names travel with a copy

As a pilot copying an existing rule set, I want the group names to come with it, so that a copied
rule set is as understandable as the original.

**Acceptance Criteria**:
- Copying a rule set preserves its group names
- The copy's names can then be edited independently of the original

## Functional Requirements

- **FR-001**: A pilot can give each condition group in their rule set a name.
- **FR-002**: A group's name persists across saving and reloading.
- **FR-003**: A group's name is displayed wherever that group is presented during editing.
- **FR-004**: Naming is optional. A group with no name behaves exactly as it does today.
- **FR-005**: Groups that exist today, created before this feature, continue to work and appear unnamed.
- **FR-006**: Naming or renaming a group never alters the decision the rule set produces.
- **FR-007**: A group's name is available to anything presenting that rule set's decision, so a decision can be explained by name.
- **FR-008**: Copying a rule set preserves group names; the copy's names are thereafter independent.
- **FR-009**: A group's name is visible only to those who can already see the rule set's conditions. It is never exposed to unauthenticated visitors. (See `002-public-rulesets` D2.)
- **FR-010**: A condition group is a thing the pilot creates, names, and fills. It keeps its identity and its name regardless of how many conditions it holds — including one, or none. A group is never silently discarded.
- **FR-011**: A group holding **no** conditions is **inert**: it has no effect whatsoever on the rule set's decision. It is not treated as satisfied, and it does not make a site red. It is a container the pilot has not filled yet, and the rule set must evaluate exactly as if it were not there.
- **FR-012**: A group holding **one** condition produces the same decision as that condition standing alone. Naming it, or grouping it, changes nothing about the outcome — only about how it is presented.
- **FR-013**: A pilot can delete a group. Deleting a group must not silently delete or orphan the conditions inside it; the pilot is told what will happen to them.

## Non-Functional Requirements

- **NFR-001 (Compatibility)**: Existing rule sets must keep producing identical decisions after this ships. This feature is additive; a rule set that is never edited must be unaffected.
- **NFR-002 (Data safety)**: No existing condition or grouping may be lost when rule sets are upgraded to carry names.

## Success Criteria

- A pilot opening a rule set they wrote months ago can identify what each group is for without reading the individual conditions.
- Every existing rule set produces the same decision after the change as before it.
- No existing group loses its members or its grouping.
- A named group's name can be used to explain a decision in place of a list of raw values.

## Key Entities

| Entity | Key Attributes | Notes |
|--------|---------------|-------|
| **Condition group** | Name (optional), belongs to one rule set, holds zero or more conditions, has an order | **New.** Does not exist as a stored entity today — grouping is currently emergent, inferred from conditions sharing an opaque marker. Per D1 this becomes a real thing the pilot creates and names, and it survives independently of how many conditions it holds |
| Condition | Belongs to at most one group | Membership becomes a real relationship to a real group, rather than a shared opaque marker |
| Rule set | Contains conditions and groups | Groups are owned by the rule set and travel with a copy of it |

## Out of Scope

- **Redesigning the map popup or any tooltip.** That is a separate change which *consumes* what this feature produces. This feature ends at making the name available.
- Nested groups, or groups within groups.
- OR-logic between group members. Groups remain AND-only, exactly as today.
- Reordering groups, or moving conditions between groups by any new mechanism.
- Naming individual conditions.
- Any change to how decisions are calculated.
- Exposing names to unauthenticated visitors (explicitly excluded by `002-public-rulesets` D2).

## Assumptions

- Names are **optional**, not required. Requiring them would force every existing group to be named before its rule set could be saved.
- Names are **not required to be unique** within a rule set. The name is a memo, not an identifier; two groups may both be called "Wind".
- A name has a **sensible maximum length** consistent with other free-text names in the product, and is plain text with no formatting.
- Group names follow the **existing permissions** of the rule set they belong to. Anyone who may edit the rule set may name its groups; anyone who may see its conditions may see its names.
- An **unnamed group displays as it does today** — no automatically generated label such as "Group 1" is invented, since a fake name is worse than none.
- The name is stored with the rule set and **travels with a copy** (FR-008), consistent with how conditions already travel.
- This feature does **not** depend on `002-public-rulesets`, and `002` does not depend on it. They touch the same display surface but ship independently.

## Dependencies

- None blocking. Related: `002-public-rulesets` D2 fixes that names must never reach anonymous visitors (FR-009).
- The separate tooltip change is a **consumer** of this feature and is best sequenced after it, to avoid designing the same display twice.

## Edge Cases

- **A group with one condition.** Keeps its identity and its name (D1). Produces the same decision as that condition standing alone (FR-012). This is a change from today, where the group would be silently discarded on save.
- **A group with no conditions.** Permitted, and inert — no effect on the decision (FR-011).
- **A group is emptied after being named** — it persists as an empty, inert group with its name intact. The pilot's typed name is never destroyed as a side-effect of removing conditions.
- **A group is deleted while holding conditions** — the pilot is told what happens to those conditions before it occurs (FR-013); they are never silently destroyed or orphaned.
- **Legacy groups** created before this feature appear unnamed and keep working (FR-005). Their existing grouping and decisions must be preserved exactly (NFR-001, NFR-002).
- **Renaming during evaluation** — a rename must never change an in-flight or subsequent decision (FR-006).
- **A named group whose stations report no data** — unchanged from today's no-data handling; naming is irrelevant to it.
- **Copying a rule set that has unnamed groups** — the copy also has unnamed groups; nothing is invented.

## Decisions

Resolved 2026-07-16. Recorded with rationale so they are not silently re-litigated later.

**D1 (FR-010 – FR-013) — Condition groups become first-class objects.**

A group is a thing the pilot creates, names, and fills — not a side-effect of conditions sharing a
marker. It keeps its name whether it holds five conditions, one, or none.

Rationale: the feature was asked for as "a name that reminds me which risk I'm focusing on". That
intent does not evaporate when a group happens to contain a single condition, so a model that
discards the name in that case fails the actual requirement. It also repairs the collapse defect
below rather than preserving it, and puts the name in one place instead of copying it onto every
member.

Cost, accepted knowingly: a real group entity, a migration for existing rule sets, and the empty-group
semantics that FR-011 now pins down.

**Consequence — the collapse defect is fixed, not preserved.**
Today a group that drops below two conditions is silently discarded when saved, with no warning. This
is currently invisible because it cannot change any decision (a one-condition group and a standalone
condition are equivalent by construction). Under D1 groups persist at any size, so the defect
disappears as a direct result rather than needing a separate fix.

**Consequence — empty groups must be inert (FR-011).**
Making groups first-class makes empty groups reachable for the first time. An empty group must have
no influence on the decision at all. Two wrong answers are specifically ruled out: an empty group must
not be treated as "satisfied" (a container the pilot has not filled cannot be evidence of anything),
and it must not break evaluation. See the plan notes for why this is a live hazard rather than a
theoretical one.
