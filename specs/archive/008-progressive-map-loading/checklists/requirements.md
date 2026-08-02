# Specification Quality Checklist: Viewport-First Progressive Loading & Geolocation Centering

**Created**: 2026-08-01
**Feature**: [spec.md](../spec.md)

## Content Quality
- [x] No implementation details dictating HOW (bbox-vs-client-side-scoping explicitly deferred to
      `plan.md`, Out of Scope)
- [x] Focused on user value and business needs (fast first paint scoped to what's on screen; a pilot
      sees their own area by default instead of always Interlaken)
- [x] All mandatory sections completed

## Requirement Completeness
- [x] No `[NEEDS CLARIFICATION]` markers — the two candidate ambiguities were resolved directly with
      the user before drafting: off-screen prefetch follows the identical time-alternation pattern,
      deprioritized (not a simpler now-only variant); a granted location preference is remembered
      across visits rather than re-asked every session.
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable and technology-agnostic
- [x] All acceptance scenarios are defined (P1 visible-now, P2 visible-other-days, P3 off-screen warm,
      P4 grant, P5 decline, P6 change-of-mind)
- [x] Edge cases are identified
- [x] Dependencies and assumptions identified

## Feature Readiness
- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] No implementation details leak into specification

## Verdict

**Ready for `plan.md`.** 12/12.

## Pre-existing gaps this spec surfaced (not introduced by it)

- **No viewport/bounds concept exists anywhere in the frontend today.** Zero uses of
  `map.getBounds()`/`.contains()` for Leaflet purposes in any `static/*.js` or `static/*.html` file.
  `plan.md` is building this from scratch, not extending something partial.
- **No server-side filtering exists on either station endpoint.** `list_stations()`
  (`stations.py:296-353`) takes only `request: Request` — no query params are actually implemented,
  despite `.ai/context/architecture.md:112` documenting `?network=&canton=` as if they exist. This is
  a stale-docs-vs-code mismatch worth fixing regardless of this feature, flagged here so `plan.md`
  doesn't assume filtering machinery is already half-built.
- **Two independent, uncoordinated TTLs already exist for replay data**: 5 min server-side
  (`_TTLCache`, `stations.py:68-69`) and 10 min client-side (`ReplayEngine`, `replay.js:26`). FR-006
  says to extend this pattern rather than add a third, but `plan.md` should decide whether the
  mismatched TTLs are worth reconciling while already in this code, or left alone as out of scope.
- **No documented total station count.** Scale claims in the spec's Assumptions section
  ("unspecified, plausibly low hundreds") are inferred from incremental per-network additions in
  `.ai/context/features.md`, not a authoritative count. `plan.md` should pull an actual count
  (station registry row count) before sizing cache limits (NFR-003) or deciding whether this
  optimization matters as much for the live endpoint as it clearly does for replay.

## Notes carried forward to `plan.md` (not spec concerns)

- `loadStations()` (`map.js:372-410`) and `ReplayEngine` (`replay.js`) are the two call sites that
  need viewport-awareness. Both currently assume "all stations" as the unit of work; introducing a
  priority queue touches both.
- The existing `_prefetchAbort` `AbortController` pattern (referenced from `specs/007`'s notes,
  `index.html:568`) is a precedent for canceling stale in-flight requests — likely reusable for
  FR-005's re-prioritization instead of inventing a new cancellation mechanism.
- Geolocation's interaction with first-paint timing (NFR-004) likely means: paint Interlaken
  immediately and unconditionally, kick off geolocation resolution in parallel, and only recenter +
  re-run the viewport-priority queue if/when it resolves — never gate the first paint on the
  permission dialog being answered. This sequencing was treated as the acceptance bar (NFR-004) but
  the exact mechanism is left to `plan.md`.
- Confirm in `plan.md` whether Lenticularis' deployment is consistently served over HTTPS everywhere
  the map is reachable (including local dev) — the Geolocation API silently unavailable on an
  insecure context should degrade like a decline (per Edge Cases), not surface as a confusing bug
  report from a dev environment.
