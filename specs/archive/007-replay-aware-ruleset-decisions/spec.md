# Feature: Replay-Aware Ruleset Decisions

**Created**: 2026-08-01
**Status**: Drafted — 0 open questions, ready for `plan.md`
**Next step**: `plan.md`

## Overview

The map's time-navigation bar (`static/index.html`) lets a pilot scrub to any day/hour (past or
forecast) or hit ▶ Play to animate through a day, and the weather markers (wind arrows, station
readings) update accordingly via `_mapReplay`'s `onFrame` → `applyReplaySnapshot()`
(`static/map.js:437`). The launch/landing/opportunity ruleset markers do not: `loadRulesetMarkers()`
(`static/index.html:765`) always calls `GET /api/rulesets/{id}/evaluate` with no `at_time`, once on
page load and again every 60 s (`index.html:932-933`), fully disconnected from the time-nav bar.
Scrubbing to yesterday, tomorrow, or pressing Play changes the wind arrows on screen but leaves every
launch/landing/opportunity dot showing today's live decision.

The backend already has most of what a fix needs: `GET /api/rulesets/{id}/evaluate?at_time=...` →
`run_evaluation_at()` (`rules/evaluator.py:472`) evaluates against **observed** data in a ±30 min
window around a past timestamp, read-only. There is no equivalent single-timestamp lookup against
**forecast** data — `run_forecast_evaluation()` (`evaluator.py:625`) only returns a whole multi-hour
series, consumed today by the ruleset-analysis chart, not by anything that needs "the decision at
exactly this moment."

This feature makes the ruleset markers on the map follow whatever moment the time-nav bar is
currently showing — past (observed), present (live), or future (forecast) — the same way the weather
markers already do.

## User Stories

### P1 — Ruleset markers reflect the selected replay moment

As a pilot scrubbing to a past day/hour, I want the launch/landing/opportunity markers to show the
decision computed from weather data **at that moment**, not "right now", so that what I see on
screen is internally consistent — the wind arrow and the decision dot agree.

**Acceptance Criteria**:
- Selecting a day offset and hour in the past re-evaluates every positioned ruleset against observed
  data at that timestamp and updates marker colour/icon accordingly.
- The decision shown matches what `GET /api/rulesets/{id}/evaluate?at_time=<that timestamp>` returns
  today (no new decision logic — same fail-safe/worst-wins semantics as v1.20.1).
- Returning to "Now" resumes live evaluation and the existing 60 s poll.

### P2 — Ruleset markers follow forecast scrubbing too

As a pilot scrubbing into tomorrow (or any hour within the forecast horizon), I want the markers to
show the forecast-based decision for that hour, so planning a trip a day out is as informative as
checking today.

**Acceptance Criteria**:
- Selecting a future day/hour re-evaluates every positioned ruleset against forecast data for that
  specific `valid_time` and updates markers.
- A ruleset with no forecast data available at that `valid_time` degrades the same way "no data"
  already does for live/observed evaluation (fail-safe red for a GREEN requirement, silent for
  exception conditions, `no_data_stations` populated) — no new no-data behaviour is invented.

### P3 — Play animates decisions, not just wind arrows

As a pilot watching ▶ Play step through a day, I want the marker colours to change in step with the
wind arrows, so the animation tells the whole story, not half of it.

**Acceptance Criteria**:
- During playback, ruleset markers update as the animation advances through hours, without visibly
  stalling the 600 ms/frame cadence or hammering the backend with redundant requests.
- Pausing or stopping Play leaves markers showing the decision for the frame playback stopped on.

## Functional Requirements

- **FR-001**: When the time-nav bar is not in Live mode (a day offset is selected, a custom date is
  selected, or Play is active), ruleset marker decisions are computed for the currently displayed
  timestamp instead of "now".
- **FR-002**: For a displayed timestamp with observed data available (past, within the existing
  ±30 min snapshot window), decisions use the existing `at_time` observed-data path
  (`run_evaluation_at`) — unchanged.
- **FR-003**: For a displayed timestamp in the forecast horizon (future, no observed data yet),
  decisions use forecast data for that specific `valid_time`. This requires a single-timestamp
  forecast lookup that does not exist yet (`run_forecast_evaluation` only returns a full series).
- **FR-004**: Landing rulesets linked from a launch site (the halo colour, `evaluate_ruleset` in
  `rulesets.py:251-269`) are evaluated at the **same** displayed timestamp as the launch site they're
  attached to. Today they are always evaluated live even when the launch site itself is evaluated
  with `at_time` — this is an existing inconsistency this feature closes as part of the same fix,
  since it's the same halo the pilot is looking at.
- **FR-005**: Returning to Live ("Now") stops timestamp-pinned evaluation and resumes the existing
  continuous live evaluation + 60 s poll, unchanged from today.
- **FR-006**: Opportunity markers (hidden unless the decision is fully green, `index.html:794-803`)
  re-derive their shown/hidden state from the displayed timestamp's decision, not the live one.
- **FR-007**: No evaluation-logic change. Decision colour, fail-safe requirement semantics
  (v1.20.1), `worst_wins`/`majority_vote`, and no-data handling are identical to today — only the
  timestamp fed into evaluation changes.

## Non-Functional Requirements

- **NFR-001 (Responsiveness)**: Scrubbing to a day/hour must not make the map feel broken while
  markers catch up — a brief, visible "updating" state is acceptable; a multi-second freeze is not.
- **NFR-002 (Backend load)**: Play animates 13 hourly frames per day at 600 ms/frame
  (`replay-playback.md`); re-evaluating every positioned ruleset on every single frame must not
  produce request volume that scales badly with rule-set count. The exact throttling mechanism
  (evaluate every frame vs. on frame-settle vs. debounced) is a `plan.md` decision, not fixed here.
- **NFR-003 (Stale-response safety)**: If the pilot scrubs again before a prior evaluation request for
  an earlier timestamp has returned, the stale response must not overwrite markers with the wrong
  timestamp's decision.
- **NFR-004 (No scope creep)**: No API shape change to the decision payload itself, no new storage,
  no change to how live decisions are written to InfluxDB (`write_decision`, `evaluate_ruleset`,
  `rulesets.py:245-248`, still only happens in the no-`at_time` live path).

## Success Criteria

- Scrubbing to a past hour shows marker decisions matching what `/api/rulesets/{id}/evaluate?at_time=`
  already computes for that hour today.
- Scrubbing to a forecast hour shows marker decisions consistent with the corresponding step of
  `GET /api/rulesets/{id}/forecast`.
- Pressing ▶ Play shows marker colours changing in sync with the wind-arrow animation already
  visible, for the whole 07:00–19:00 window.
- Returning to "Now" restores live-updating markers within one poll cycle, identical to current
  behaviour.
- A launch site's landing halo agrees with its linked landing site's own marker colour at the same
  displayed timestamp.

## Out of Scope

- Any change to the evaluator's decision logic itself (fail-safe requirements, `worst_wins`,
  `majority_vote`, no-data handling) — that is v1.20.1 territory and stays untouched (FR-007).
- Persisting per-frame historical decisions to InfluxDB. The `at_time` and forecast lookups stay
  read-only/ephemeral, exactly as `run_evaluation_at` already is.
- The `/ruleset-analysis` page's own forecast chart (`get_forecast`, `run_forecast_evaluation` full
  series) — already time-aware by design, untouched.
- The public, anonymous map view's own decision freshness — `is_public` gating (specs/002) is
  unaffected; this feature only changes *which timestamp* a marker's decision reflects for whoever
  can already see it, not who can see it.
- `specs/006-thermal-forecast` — unrelated forecast data source, no overlap.

## Assumptions

- The existing observed-data (`at_time`) and forecast-series (`run_forecast_evaluation`) paths are
  the right foundations; this feature adds a single-timestamp forecast lookup rather than a new data
  pipeline.
- It is acceptable for Play to throttle/debounce marker re-evaluation rather than guarantee
  frame-perfect sync with the wind-arrow animation, given up to dozens of rulesets × 13 frames/day.
- The time-nav bar and replay only exist on the authenticated map (`index.html`); no other page needs
  this wiring.

## Dependencies

- `run_evaluation_at` (`rules/evaluator.py:472`) — observed-data historical evaluation, reused as-is.
- `query_forecast_for_stations` (used by `run_forecast_evaluation`) — the forecast data source a new
  single-timestamp lookup would read from.
- `_mapReplay` / `ReplayEngine` (`static/replay.js`) — already exposes the current timestamp and
  whether it's a forecast frame (`isForecast`) on every `onFrame` call; this is the hook the new
  marker-refresh logic attaches to.

## Edge Cases

- Selected timestamp has no observed data and no nearby forecast data → same fail-safe "no data"
  behaviour as today's live evaluation (FR-007), nothing new invented.
- Day is far enough in the past that some stations have observed data and others don't (mixed
  no-data) → per-station `no_data_stations`, exactly as `at_time` already handles it.
- lsmfapi's forecast run is stale or missing for the selected forecast hour → same no-data fail-safe.
- Pilot scrubs rapidly through several hours before any evaluation returns → last selection wins
  (NFR-003); no flicker back to a stale timestamp's decision.
- Opportunity marker that was hidden live becomes visible at the replayed timestamp (or vice versa) →
  hidden/shown state is recomputed per FR-006, not carried over from the live state.
- Landing site linked to a launch site has no data at the replayed timestamp → halo reflects that
  no-data state rather than silently falling back to the linked site's live decision.
