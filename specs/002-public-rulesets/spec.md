# Feature: Public Rule Sets on the Map

**Created**: 2026-07-16
**Status**: Clarified — 0 open questions, ready for `plan.md`
**Next step**: `plan.md`

## Overview

Today a visitor who is not signed in sees a map of weather stations and nothing else — no rule sets,
no traffic lights, no evidence of what the product actually decides. The core value (a green/orange/red
call for a flying site) is invisible until after registration.

This feature makes rule sets that their owner has marked public visible on the map to everyone,
including visitors who are not signed in, so the product demonstrates itself before signup. It also
suppresses public rule sets for signed-in pilots who already have their own rule set at the same
site, so the map does not fill with duplicates of places they have already configured.

## User Stories

### P1 — A visitor understands the product without signing up

As an unauthenticated visitor, I want to see curated example rule sets on the map showing their
current traffic-light state, so that I understand what the product does before deciding to create
an account.

**Acceptance Criteria**:
- Curated example rule sets appear as markers on the map without signing in
- Each marker shows its current decision colour, matching what a signed-in user would see for it
- Selecting a marker shows only the site name, its location, and its current colour — plus an
  explanation that it is an example and an invitation to sign up and build their own
- No conditions, thresholds, or owner identity are revealed to an unauthenticated visitor
- A rule set that is not curated for public display never appears to an unauthenticated visitor
- If no curated rule set can currently be evaluated, the map still loads normally with stations

### P1 — A pilot is not shown duplicates of their own sites

As a signed-in pilot, I want public rule sets to appear only where I do not already have my own rule
set nearby, so that my map is not cluttered with other people's version of a site I have configured.

**Acceptance Criteria**:
- A public rule set within the proximity threshold of one of my own rule sets is not shown to me
- Outside that threshold, the public rule set is shown
- A public rule set I am shown is visually distinguishable from my own rule sets at a glance
- My own rule sets always take precedence and are never hidden

### P2 — An owner controls exposure

As a pilot, I want to decide whether my rule set is public, and to change my mind later, so that I
am never publishing my site setup without intending to.

**Acceptance Criteria**:
- Marking a rule set public is an explicit, reversible action by the owner
- Un-publishing removes it from the public map on the next refresh
- The owner can tell, from the rule set itself, whether it is currently public

### P3 — A visitor converts

As an unauthenticated visitor who has seen an example, I want a direct path to create my own version,
so that I do not have to work out where to start.

**Acceptance Criteria**:
- The example explanation includes a direct route to sign-up
- After signing up, the visitor lands somewhere that lets them create a rule set

## Functional Requirements

Two audiences see two different sets. This distinction drives the whole feature:

| Audience | Sees | Rationale |
|---|---|---|
| Unauthenticated visitor | **Curated** examples only | The public map is the shop window; its quality must not depend on what any pilot happens to publish |
| Signed-in user | Any owner-published rule set, minus those near their own | Discovery should be broad for people already invested |

- **FR-001**: Rule sets curated for public display are visible on the map to unauthenticated visitors.
- **FR-002**: Rule sets not published by their owner are never visible to anyone other than their owner (and roles already permitted to see them).
- **FR-003**: Each publicly visible rule set displays its current traffic-light decision, consistent with what its owner sees.
- **FR-004**: Selecting a public rule set while not signed in presents an explanation that it is an example, and a call to action to sign up and create one's own.
- **FR-005**: For a signed-in user, an owner-published rule set belonging to someone else is suppressed when that user already owns a rule set **within 500 m** of it.
- **FR-006**: Public rule sets shown to a signed-in user are visually distinct from that user's own rule sets.
- **FR-007**: An unauthenticated visitor is shown **only** the site name, location, and current decision colour of a curated rule set. Conditions, thresholds, group structure, and owner identity are **not** exposed.
- **FR-008**: Qualification differs by audience: unauthenticated visitors see only an **admin-curated** subset; signed-in users see **any owner-published** rule set, subject to FR-005.
- **FR-009**: Opportunity-type public rule sets follow the same visibility rule as today — shown only when fully green.
- **FR-010**: A public rule set that cannot currently be evaluated is omitted rather than shown in an unknown state.
- **FR-011**: Curation for public display is an admin action. An owner publishing a rule set does **not** by itself place it on the anonymous map — an admin must also select it.
- **FR-012**: An admin can only curate a rule set **its owner has already published**. A rule set the owner has kept private can never be placed on the anonymous map, by anyone. Owner consent is a precondition for public exposure, not a parallel opinion.
- **FR-013**: If an owner un-publishes a curated rule set, it disappears from the anonymous map. The admin's curation choice is remembered and takes effect again if the owner re-publishes — un-publishing hides it, it does not destroy the editorial decision.

## Non-Functional Requirements

- **NFR-001 (Privacy)**: The public view must not expose the identity or personal details of a rule set's owner unless the owner has consented to that.
- **NFR-002 (Abuse resistance)**: An anonymous visitor must not be able to trigger unbounded or per-item repeated work by loading or reloading the public map. The cost of serving the public map must not scale with the number of public rule sets on each view.
- **NFR-003 (Responsiveness)**: The public map must remain as responsive for a visitor as the current map is for a signed-in pilot.
- **NFR-004 (Freshness)**: Public decisions must be recent enough to be trustworthy; a visibly stale traffic light is worse than none.

## Success Criteria

- A first-time visitor, without signing in, can see at least one live example rule set with a current traffic-light colour on their first view of the map.
- Zero rule sets that were not marked public are ever visible to a non-owner.
- A signed-in pilot who has configured a site sees exactly one marker at that site — their own.
- A visitor who selects an example is told, in one sentence, what it is and what to do next.
- Adding public rule sets does not measurably slow the map for existing signed-in users.

## Key Entities

| Entity | Key Attributes | Notes |
|--------|---------------|-------|
| Rule set | Name, location, site type, owner-published flag, current decision | Already carries its own site identity and an owner-published marking |
| Curation marking | Whether a rule set is an admin-approved public example | **Distinct from the owner-published flag** (FR-011). An admin curation concept already exists for a different purpose — whether to reuse or add one is a `plan.md` decision, see Assumptions |
| Viewer | Signed-in or anonymous; own rule sets and their locations | Determines which set is shown and what is suppressed |
| Proximity rule | 500 m | Decides "I already have this site" |

## Out of Scope

- Cloning or copying a public rule set (the gallery already covers discovery for signed-in users)
- Public access to history, forecast, statistics, or any detail page
- Public access to anything other than the map view
- Changing how decisions are calculated
- Editing or commenting on someone else's public rule set
- Any change to the existing gallery behaviour for signed-in users

## Assumptions

- The existing owner-controlled published marking is the basis for the **signed-in** audience; no new publication concept is introduced for it.
- An **admin curation** marking is needed for the anonymous audience (FR-011). An admin-toggled marking already exists for a related but different purpose — designating a rule set as a starting template. Reusing it would overload one flag with two meanings (a good template is not necessarily a good showcase, and vice versa). `plan.md` decides: reuse, or add a distinct marking. Either way the *behaviour* in this spec is unchanged.
- Public rule sets refresh on the same cadence as the current map refresh; visitors and pilots see equally fresh data.
- A visitor sees public rule sets from everywhere, not filtered to a region — the map's own zoom is the only filter.
- The existing rule that opportunity sites appear only when fully green is desirable for visitors too, and is retained.
- Rule sets without a location cannot be shown on a map and are silently omitted, as today.
- Suppression by proximity applies only to *other people's* rule sets; a user's own are never suppressed by anything.
- 500 m is a fixed value, not configurable. It can be revisited once there is real usage; a config key would defer a decision rather than make one.
- The anonymous teaser (FR-007) needs no reason text. Should 003 (named condition groups) ship, a leak-free reason becomes possible and is worth revisiting — but it is **not** a dependency of this feature.

## Dependencies

- Owners must actually mark rule sets public, otherwise a visitor sees an empty map. At least one suitable public rule set must exist before this ships, or the feature demonstrates nothing.
- Resolution of Q2 (exposure depth) gates the privacy review.

## Edge Cases

- **No public rule sets exist** → visitor sees the station map exactly as today; nothing breaks.
- **Owner un-publishes while a visitor is viewing** → the rule set disappears on the next refresh; no error. The admin's curation choice survives and re-applies if the owner publishes again (FR-013).
- **Admin tries to curate a rule set the owner has not published** → refused with a clear reason, never silently accepted (FR-012).
- **Owner publishes a rule set an admin had previously curated, then un-published** → it returns to the anonymous map without any new admin action.
- **A rule set is curated and published, then its owner deletes it** → it disappears everywhere; nothing to curate.
- **Public rule set has no location** → omitted from the map.
- **Public rule set cannot be evaluated (no station data)** → omitted rather than shown grey/unknown (FR-010).
- **Signed-in user owns a public rule set** → they see it once, as their own, not twice.
- **Two public rule sets at the same site** → both shown; de-duplication between *other people's* public rule sets is out of scope.
- **A user's own rule set has no location** → it cannot suppress anything by proximity, since proximity is undefined.
- **Visitor signs in while viewing the map** → the view reconciles to the signed-in rules without requiring a manual reload.

## Decisions

Resolved 2026-07-16. Recorded with rationale so they are not silently re-litigated later.

**D1 (FR-005) — Proximity threshold is 500 m.**
A ridge frequently carries two genuinely different launches 500–1000 m apart that work in opposite
wind directions, so a larger radius would hide useful examples. The failure modes are asymmetric:
hiding a good example fails invisibly, while showing a duplicate marker is merely annoying and
self-evident. 500 m therefore errs toward showing. (The system's existing 50 m station-merge notion
is far too tight for flying sites and is unrelated.)

**D2 (FR-007) — Anonymous visitors see name, location, and colour only.**
This matches the stated intent — an example that prompts sign-up, not a tutorial. It keeps the
privacy surface minimal (NFR-001 is satisfied by construction rather than by filtering), exposes
nothing of the owner's setup, and keeps rules pilot-owned, which is the product's differentiator.
Publishing thresholds to anonymous visitors would give away the very thing the product asks pilots
to invest in.

**D3 (FR-008, FR-011) — Curated for anonymous, any published for signed-in.**
Two audiences carry two different risks. The anonymous map is a first impression and must not be
hostage to whatever a pilot last published; the signed-in map is discovery and benefits from breadth.
This costs an admin curation step and a decision in `plan.md` about which marking backs it
(see Assumptions).

**D4 (FR-012, FR-013) — Curation requires the owner to have published. Resolved 2026-07-16.**

An admin may only curate a rule set whose owner has already published it. Curation *selects from*
what owners have offered; it is not an independent right to publish someone's work.

Rationale: D3 made curation an admin decision, and taken literally that would let an admin place a
**private** rule set on the open internet — the owner never consented to publication at all. The
product's stated differentiator is that rules are pilot-owned; an admin overriding that would
contradict the thing the feature exists to advertise. Requiring publication first costs nothing real
(an admin who wants to showcase something can ask the owner to publish it) and removes the entire
class of "we published your private setup" failure.

Consequences:
- Curating an unpublished rule set is refused, not silently ignored — an admin who thinks they have
  curated something must not be wrong about that.
- Un-publishing hides a rule set from the anonymous map immediately, but **retains** the admin's
  curation choice. The owner's consent is a live gate, not a one-time handshake, and re-publishing
  restores the previous editorial state without an admin having to redo the work.
- Visibility to anonymous visitors therefore requires **both** flags, checked at read time. Neither
  alone is sufficient.
