# Feature: Green Conditions Are Requirements (Fail-Safe Launch/Landing)

**Created**: 2026-07-17
**Status**: Clarified — 0 open questions, ready for `plan.md`
**Next step**: `plan.md`

## Overview

Launch and landing sites default to **green** when nothing triggers ("benefit of the doubt",
`evaluator.py:431-433`). A condition only contributes its colour **when it matches**. That is correct
for *exception* conditions — "gust > 50 → red" should stay silent, and green, on a calm day.

It is wrong for *confirmation* conditions. The product explicitly teaches pilots to write GREEN
conditions as positive confirmation — "wind direction in the usable arc → green" (`help.html:397`).
Such a condition only fires when the direction **is** correct. When the direction is wrong, it does
not fire, nothing triggers, and the site reads **green by default** — the exact opposite of the
pilot's intent. A launch whose only rule is "direction must be usable" is therefore green 100% of the
time, including when the direction is dead wrong.

The two green outcomes are indistinguishable in the output: "green because a requirement was met" and
"green because nothing fired" both produce green. This feature separates them by making a GREEN
condition a **requirement**: if a required green condition is not met, the site is **red**.

Opportunity sites already behave this way (red by default, green only when every unit triggers,
`evaluator.py:417-423`) and are unchanged.

## User Stories

### P1 — A confirmation rule actually gates the decision

As a pilot, I want a GREEN "must be true" condition to make the site **red** when it is not true, so
that a site is never shown as flyable just because I only wrote positive rules.

**Acceptance Criteria**:
- A launch/landing site whose only condition is a GREEN confirmation reads green when that condition
  matches, and **red** when it does not.
- A site built only from RED/ORANGE exception conditions is unaffected: it still reads green on a calm
  day when nothing triggers.
- A GREEN condition that matches still contributes green exactly as before.

### P2 — Missing data fails safe, not open

As a pilot, I want a required GREEN condition whose station is offline to make the site **red**, so
that an unconfirmable requirement is never silently treated as satisfied.

**Acceptance Criteria**:
- A GREEN requirement whose station reports no data drives the site to **red**.
- `no_data_stations` still lists that station, so the pilot can see *why* it is red.
- A RED/ORANGE exception condition whose station reports no data is unchanged — it stays silent, and
  the site keeps its benefit-of-the-doubt green. Fail-safe applies to requirements only.

## Functional Requirements

- **FR-001**: For launch and landing sites, a GREEN unit (a standalone GREEN condition, or an AND
  group whose effective colour is green) that does **not** trigger contributes RED to the decision.
- **FR-002**: A GREEN unit that **does** trigger contributes green, exactly as today.
- **FR-003**: RED and ORANGE conditions keep exception semantics: they contribute their colour only
  when they match, and contribute nothing when they do not. Their no-data behaviour is unchanged.
- **FR-004**: A site with no GREEN units and no triggered exceptions still resolves to green (the
  benefit-of-the-doubt default survives for exception-only rule sets).
- **FR-005**: "Not triggered" covers both a data-present threshold failure and a no-data station.
  Both make a GREEN requirement contribute RED (fail-safe).
- **FR-006**: Opportunity sites are unchanged. The new rule is not applied to them.
- **FR-007**: The rule applies uniformly across every evaluation path — live, point-in-time snapshot,
  forecast, and history backfill — so a decision computed for the same data is identical regardless of
  which path produced it.

## Non-Functional Requirements

- **NFR-001 (Explainability)**: The change must be expressible to pilots in one sentence — "a green
  condition is a requirement; if it is not met, the site is red" — and the help text must say so.
- **NFR-002 (No silent scope creep)**: Only the launch/landing decision changes. No API shape, no
  storage, no new route.

## Success Criteria

- A launch site whose only rule is a GREEN direction confirmation is red when the direction is wrong,
  green when it is right.
- Every exception-only rule set produces the identical decision it did before this change.
- The four evaluation paths agree on the decision for the same input.

## Out of Scope

- `majority_vote` UX. The editor hardcodes `worst_wins` (`ruleset-editor.html:1225`); the new
  contribution flows through the shared `triggered_colours` list, so `majority_vote` inherits it
  without special-casing, but it is not a design target.
- Distinguishing no-data from threshold-failure in the output beyond the existing `no_data_stations`
  list and per-condition `matched`/`actual_value` fields.
- Any change to opportunity semantics.
- Refactoring the four duplicated decision blocks into one. Flagged in the plan as a follow-up.

## Decisions

Resolved 2026-07-17 with the user. Recorded so they are not silently re-litigated.

**D1 — A GREEN condition is a requirement; unmet ⇒ red.**
Chosen over (a) a global red default for all launch/landing sites, and (b) a new opt-in
`combination_logic` mode. A global flip would break every exception-style rule set (a calm day would
read red). An opt-in mode would leave the reported flaw live for everyone who does not find the
toggle. Requirement semantics fix the reported case with the least breakage: only rule sets that
actually contain a GREEN condition change, and they change in the direction the pilot intended.

**D2 — Requirement-ness is decided at unit granularity, by effective colour.**
A unit is a standalone condition, or an AND group treated as one unit with effective colour
`_worst(members)`. Only a unit whose effective colour is green is a requirement. A **mixed** group
(e.g. green + orange members, effective colour orange) stays exception-style. This mirrors how
`worst_wins` already collapses a group to a single colour, and keeps the rule stated in one line.

**D3 — No-data GREEN requirement ⇒ red (fail-safe).**
Chosen over treating no-data as "unknown, stay out". A flying-decision tool should not present an
unconfirmable requirement as satisfied. Because "no data" and "threshold failed" both surface as
"not triggered", this needs no special code path — it falls out of FR-001. `no_data_stations` still
explains the red. Benefit-of-the-doubt is deliberately preserved for exception conditions (FR-003),
so this does **not** turn every offline station red — only offline stations behind a GREEN
requirement.

## Edge Cases

- **Green condition, data present, threshold met** → green contribution (unchanged).
- **Green condition, data present, threshold failed** → red contribution (new).
- **Green condition, no data** → red contribution (new, D3).
- **Red/orange condition, no data** → silent; site stays benefit-of-the-doubt green (unchanged).
- **All-green AND group, not all members match** → red contribution (FR-001).
- **Mixed AND group (worst = orange/red), not all match** → silent (D2, unchanged).
- **Exception-only rule set, calm day** → green (FR-004, unchanged).
- **Opportunity site** → unchanged (FR-006).
- **Empty rule set** → green (no units, unchanged).
