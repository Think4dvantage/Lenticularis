# Specification Quality Checklist: Public Rule Sets on the Map

**Created**: 2026-07-16
**Feature**: [spec.md](../spec.md)

## Content Quality

- [x] No implementation details (languages, frameworks, APIs)
- [x] Focused on user value and business needs
- [x] All mandatory sections completed

## Requirement Completeness

- [x] **No [NEEDS CLARIFICATION] markers remain** — all 3 resolved 2026-07-16 and recorded as D1–D3 in the spec. A fourth decision (**D4**, owner consent gates curation) was raised during planning and resolved the same day, adding FR-012 and FR-013
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

**Ready for `plan.md`.** 12/12. All three clarifications resolved and recorded with rationale (D1–D3),
which turned FR-005, FR-007 and FR-008 from placeholders into testable requirements and added FR-011.

Scope grew slightly during clarification, honestly: D3 introduced a two-audience model, so this is no
longer "show public rule sets" but "show *curated* rule sets to visitors and *published* ones to
members". That is a larger build than the original request implied, and `plan.md` must size the admin
curation surface (FR-011) as part of it.

## Decisions resolved during planning — none outstanding

- **Which marking backs curation (FR-011).** Resolved in research R1: a **new** `is_showcase` column.
  Reusing `is_preset` would weld "good template" to "good showcase" — two unrelated editorial
  judgements — and reusing `is_public` would make one flag carry two different people's decisions.
- **Does curation require owner consent?** Resolved as **D4**: yes. Anonymous visibility is
  `is_showcase AND is_public`, checked at read time, with a 409 write guard. An admin cannot place a
  private rule set on the public internet. Added FR-012 and FR-013.

**No open questions remain. Ready for `tasks.md`.**

## Notes carried forward to `plan.md` (not spec concerns)

These are implementation constraints found during recon. They are deliberately absent from the spec
per `specify.md` ("never HOW"), and must not be lost:

- The current map builds rule set markers with a per-rule-set evaluation call in a loop
  (`static/index.html:770-772`). Serving that shape to anonymous visitors would create an
  unauthenticated N+1 fan-out into the time-series database. `04-constraints.md` (T09,
  "Batch before looping") forbids this pattern even when authenticated. NFR-002 exists to force
  a batched, cached public path — the same shape `/api/stations/replay` already uses.
- The entire rule set layer is currently gated behind `if (isLoggedIn())`
  (`static/index.html:573-815`); the anonymous path does not exist at all today.
- `get_current_user_optional` already exists for endpoints with both a public and an authenticated
  view — but note it does **not** check `is_active` (see `02-backend-conventions.md`), which matters
  if it is used on a path that must distinguish a live account from a disabled one.
- Owner display name is currently part of the standard rule set payload. D2 forbids exposing it to
  anonymous visitors, so the public path needs its own narrower payload rather than reusing the
  existing one — reuse would leak owner identity by default.
- D1's 500 m test needs a distance calculation between a viewer's rule sets and candidate public
  ones. **Reuse `haversine_m(lat1, lon1, lat2, lon2)` at `services/dedup.py:44`** — it is already
  public and already returns metres, so the 500 m test is a direct comparison. Do not reimplement it
  (`02-backend-conventions.md` forbids redefining shared helpers).
- Note the naive comparison is O(viewer's rule sets × candidate public rule sets) per request. At
  current scale that is trivial, but it sits behind the cache required by NFR-002, so it must not be
  computed per anonymous request.

## Notes carried forward to `plan.md` (not spec concerns)

These are implementation constraints found during recon. They are deliberately absent from the spec
per `specify.md` ("never HOW"), and must not be lost:

- The current map builds rule set markers with a per-rule-set evaluation call in a loop
  (`static/index.html:770-772`). Serving that shape to anonymous visitors would create an
  unauthenticated N+1 fan-out into the time-series database. `04-constraints.md` (T09,
  "Batch before looping") forbids this pattern even when authenticated. NFR-002 exists to force
  a batched, cached public path — the same shape `/api/stations/replay` already uses.
- The entire rule set layer is currently gated behind `if (isLoggedIn())`
  (`static/index.html:573-815`); the anonymous path does not exist at all today.
- `get_current_user_optional` already exists for endpoints with both a public and an authenticated
  view — but note it does **not** check `is_active` (see `02-backend-conventions.md`), which matters
  if it is used on a path that must distinguish a live account from a disabled one.
- Owner display name is currently part of the standard rule set payload; Q2/NFR-001 decide whether
  it must be stripped on the public path.
