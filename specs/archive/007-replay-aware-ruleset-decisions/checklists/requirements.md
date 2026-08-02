# Specification Quality Checklist: Replay-Aware Ruleset Decisions

**Created**: 2026-08-01
**Feature**: [spec.md](../spec.md)

## Content Quality
- [x] No implementation details dictating HOW (throttling mechanism explicitly deferred to `plan.md`,
      NFR-002)
- [x] Focused on user value and business needs (map internal consistency: dot colour agrees with
      wind arrow, for any point in time the pilot is looking at)
- [x] All mandatory sections completed

## Requirement Completeness
- [x] No `[NEEDS CLARIFICATION]` markers — the two candidate ambiguities (Play-frame throttling
      mechanism; whether to fold in the landing-halo live-evaluation inconsistency) were resolved by
      inline decision rather than deferred: throttling is explicitly punted to `plan.md` as a HOW
      question (NFR-002), and the halo fix is folded into scope (FR-004) since it's the same UI
      element this feature already touches.
- [x] Requirements are testable and unambiguous
- [x] Success criteria are measurable and technology-agnostic (compare against existing endpoints'
      current output, not internal implementation)
- [x] All acceptance scenarios are defined (P1 past, P2 forecast, P3 Play animation)
- [x] Edge cases are identified
- [x] Dependencies and assumptions identified

## Feature Readiness
- [x] All functional requirements have clear acceptance criteria
- [x] User scenarios cover primary flows
- [x] No implementation details leak into specification

## Verdict

**Ready for `plan.md`.** 12/12.

## Pre-existing defect folded into scope

Found while reading the current evaluate path, not introduced by this feature: `evaluate_ruleset`
(`rulesets.py:251-269`) always evaluates a launch site's linked landing rulesets live
(`run_evaluation`), even when the launch site itself is evaluated historically via `at_time`
(`run_evaluation_at`). Today this is silently wrong only in the sense that nothing currently calls
`evaluate?at_time=` from the map, so it has never been visibly inconsistent. The moment FR-001 makes
the map call `at_time` for replay, this becomes visibly wrong (halo shows live weather, dot shows
replayed weather) unless fixed alongside it — hence FR-004 pulls it into scope rather than leaving it
as a separate follow-up bug.

## Notes carried forward to `plan.md` (not spec concerns)

- No single-timestamp forecast evaluation function exists yet. `plan.md` needs to design one,
  reusing `query_forecast_for_stations` (or the underlying InfluxDB query it wraps) filtered to the
  nearest `valid_time`, and reusing the identical condition/group/combination logic that
  `run_evaluation_at` and `run_forecast_evaluation` already duplicate a third time. Consider whether
  this is the moment to extract that shared logic instead of copying it again — flagged, not decided
  here (spec stays HOW-free).
- `loadRulesetMarkers()` (`index.html:765`) is currently a fire-and-forget async function called from
  a `setInterval` and from day/hour click handlers with no cancellation. Wiring it to `_mapReplay`'s
  `onFrame` needs an in-flight-request guard or generation counter to satisfy NFR-003 — the existing
  function has no such guard today because it never had overlapping calls to worry about.
  `_prefetchAbort` (`index.html:568`) shows the codebase already has a precedent pattern
  (`AbortController`) for exactly this kind of cancellation.
- `_mapReplay`'s `onFrame` callback signature already carries everything needed:
  `(snapshot, ts, idx, total, isForecast)` (`static/index.html:319`) — `ts` and `isForecast` are the
  two inputs a replay-aware `loadRulesetMarkers(ts, isForecast)` needs.
- Live mode currently has no single "am I live" flag passed around explicitly — it's tracked via
  `_tnLive`/`_tnOffset`/`_tnCustomDate` module-level variables in `index.html`. `plan.md` should
  decide whether replay-aware marker loading reads these directly or whether `_mapReplay` should
  expose its own `isLive()`/current-timestamp getter so `map.js` doesn't need to reach into
  `index.html`'s time-nav state.
