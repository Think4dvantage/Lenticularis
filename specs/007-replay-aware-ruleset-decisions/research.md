# Research: Replay-Aware Ruleset Decisions

**Feature**: [spec.md](./spec.md)
**Phase**: 0 — Research
**Date**: 2026-08-01

Unknowns identified in the spec, resolved here before design.

---

## R1 — Where does the single-timestamp forecast lookup come from? (FR-003)

- **Decision**: Reuse `InfluxClient.query_forecast_snapshot_for_stations(station_ids, valid_time)`
  (`database/influx.py:280-327`) — it **already exists** and already does exactly what FR-003 asks
  for: one batched Flux query, ±30 min window around `valid_time` in `weather_forecast`, one row per
  station. Build a new evaluator entry point `run_forecast_evaluation_at()` around it.
- **Rationale**: The spec and the checklist both say "no single-timestamp forecast evaluation
  function exists yet" — true at the **evaluator** layer (`run_forecast_evaluation` only returns a
  whole series), but the **InfluxDB query** layer already has this function, and it already has a
  live caller: `routers/foehn.py:177-199` (`GET /api/foehn/forecast?valid_time=...`) evaluates föhn
  regions from a single forecast snapshot the same way this feature needs to evaluate rule sets.
  That endpoint is the working precedent for "forecast decision at exactly this moment" — it proves
  the query shape is sound in production, not just in theory.
- **Alternatives considered**:
  - *Write a new Flux query from scratch* — duplicates `query_forecast_snapshot_for_stations`
    almost verbatim. No reason to, and it would be a second near-identical query to maintain.
  - *Reuse `query_forecast_for_stations` (the full-series query) and pick the nearest step* —
    wasteful: it fetches the whole horizon (`run_forecast_evaluation`'s data source) just to throw
    away all but one `valid_time`. The snapshot query is the right grain for a single lookup.

## R2 — Does the new evaluator function duplicate the decision logic a fifth time?

- **Decision**: No. `run_forecast_evaluation_at()` builds `station_data` from the snapshot query,
  then calls the existing shared core `_evaluate_from_station_data(ruleset, station_data)`
  (`evaluator.py:207-303`) for the actual decision — it does **not** copy the
  standalone/group/combination-logic block inline the way `run_evaluation`, `run_evaluation_at`, and
  `run_forecast_evaluation` each already do.
- **Rationale**: `architecture.md` already flags that the decision rule "is duplicated across all
  four decision blocks... a flagged follow-up is to route them through the shared core." Only
  `run_history_backfill` currently calls `_evaluate_from_station_data`; the other three duplicate it
  inline (pre-dating the extraction). This feature is additive — a **new** function — so it is free
  to call the shared core from the start rather than becoming the fifth copy. This does **not**
  refactor the three pre-existing duplicates (out of scope, FR-007/NFR-004 — no evaluation-logic
  change, no scope creep) — it just avoids adding a sixth place the rule would need to be kept in
  sync.
- **Consequence**: `run_forecast_evaluation_at()` is caller-responsible for building `station_data`
  and the `no_data_stations` list (per `_evaluate_from_station_data`'s existing contract — "the
  caller is responsible for building `station_data` and recording `no_data_stations`"), exactly as
  `run_evaluation_at` already is, just without calling it.
- **Alternatives considered**: *Copy the inline block a fifth time, matching the other three* —
  keeps some superficial consistency across the file, but bakes in one more place a future rule
  change (e.g. specs/004's GREEN-requirement fix) has to be remembered and applied identically. Not
  worth it for new code with no existing callers to stay bug-compatible with.

## R3 — How does `/evaluate` distinguish "observed at_time" from "forecast at_time"? (FR-002 vs FR-003)

- **Decision**: Add a new optional query param `forecast: bool = False` alongside the existing
  `at_time`. The client states which mode it wants; the server does not infer it from comparing
  `at_time` to "now".
- **Rationale**: The frontend already knows unambiguously which mode applies to the frame it is
  displaying — `ReplayEngine.isForecastFrame` / the `isForecast` argument `onFrame` already receives
  (`static/replay.js:219-224`, `246`) is precisely "is this frame forecast or observed," derived
  server-side by `/api/stations/replay` from its own `forecast_from` boundary. Re-deriving the same
  answer a second time in `evaluate_ruleset` by comparing `at_time` to `datetime.now()` risks
  disagreeing with the wind-arrow snapshot at the exact boundary (clock skew between request time and
  `at_time`, or a `valid_time` a few minutes into the future that still has an observed reading in the
  ±30 min snapshot window) — exactly the kind of internal-consistency bug this feature exists to
  remove (P1's acceptance criterion: "the wind arrow and the decision dot agree"). Passing the mode
  explicitly means the ruleset decision always uses the *same* classification the map already used to
  decide which snapshot to draw.
- **Consequence**: `EvaluationResult`'s response shape is **unchanged** (NFR-004) — only a new,
  optional request parameter. Existing callers that never pass `forecast` keep today's behaviour
  (`at_time` set → `run_evaluation_at`, unchanged, FR-002) with no default-value migration needed.
- **Alternatives considered**:
  - *Server infers from `at_time` vs `now()`* — one fewer query param, but reintroduces exactly the
    boundary-disagreement risk above, and duplicates classification logic the replay endpoint already
    computed once.
  - *Two separate endpoints* (`/evaluate` and `/evaluate/forecast`) — no shape change either, but the
    frontend already calls one URL per positioned ruleset per refresh; a second endpoint doubles the
    branching in `loadRulesetMarkers()` for no benefit over one extra query param on the existing call.

## R4 — Fixing the landing-halo live-evaluation inconsistency (FR-004)

- **Decision**: Factor `evaluate_ruleset`'s "which evaluation function for this request" branch
  (`rulesets.py:237-248`) into a small helper, e.g. `_evaluate_at(rs, influx, at_time, forecast,
  virtual_members)`, and call that same helper for the linked landing rulesets
  (`rulesets.py:251-269`) instead of the unconditional `run_evaluation(landing_rs, influx,
  virtual_members)` at line 258.
- **Rationale**: This is the pre-existing defect the checklist already found and folded into scope
  (FR-004): the halo is evaluated live even when the launch site itself is evaluated with `at_time`.
  It was never visibly wrong before because nothing called `/evaluate?at_time=` from the map; FR-001
  makes that call exist, so the mismatch becomes visible the moment this feature ships unless fixed
  alongside it. A shared helper also means the three-way branch (live / observed-at_time /
  forecast-at_time) is written once, not twice.
- **Alternatives considered**: *Leave the halo live and accept the mismatch* — rejected by the spec
  itself (FR-004, Success Criteria's last bullet: "A launch site's landing halo agrees with its linked
  landing site's own marker colour at the same displayed timestamp").

## R5 — How does the frontend avoid a request storm during Play, without dropping stale-response safety? (NFR-002, NFR-003)

- **Decision**: A single-slot coalescing queue, not a fixed debounce and not per-request
  `AbortController`s. Each `onFrame` call records `(ts, isForecast)` as the "latest wanted" target.
  If a `loadRulesetMarkers` batch is already in flight, the new frame's target simply overwrites the
  pending slot and returns immediately — no new fetch batch starts. When the in-flight batch resolves,
  if the pending slot holds a target newer than what was just fetched, immediately kick off the next
  batch for that (skipping every frame in between); otherwise go idle. A monotonically increasing
  generation counter (`_rulesetMarkerGen`) is stamped at the start of each batch and checked before the
  batch is allowed to touch `rulesetLayer` — a batch whose generation has been superseded discards its
  results instead of rendering them.
- **Rationale**:
  - **Never more than one batch of `positioned.length` fetches in flight at once** — this is what
    keeps request volume from scaling with rule-set count × frame rate (NFR-002) regardless of how
    fast frames arrive (600 ms/frame during Play, `static/index.html:530`) or how slow the backend is.
  - **A fixed debounce delay doesn't adapt to backend latency**: pick it too short and slow responses
    still overlap; pick it too long and P3's "colours change in step with the wind arrows" acceptance
    criterion suffers on a fast, healthy backend. The coalescing queue adapts automatically — as fast
    as the backend allows, never faster.
  - **The generation counter, not per-request `AbortController`s, guards NFR-003.** `loadRulesetMarkers`
    already fans out via `Promise.allSettled` over `positioned.length` independent fetches
    (`index.html:776-778`); aborting each one individually on every supersede is more moving parts for
    the same outcome as "check a counter before rendering, discard if stale" — abort saves the network
    round-trip, discard-after-the-fact does not, but for a handful of small `/evaluate` calls behind a
    5-frames-per-3-seconds cadence this is not worth the extra bookkeeping. `_prefetchAbort`
    (`index.html:553-568`) remains the pattern for the one long-lived background prefetch loop it
    already guards — a different shape of problem (cancel-on-navigate-away, not
    supersede-mid-animation).
- **Consequence**: Live mode is unaffected — the existing unconditional 60 s `setInterval` call
  (`index.html:933`) is not itself a coalescing target; it only ever fires when idle, since the poll
  interval (60 s) is far longer than any plausible batch duration.
- **Alternatives considered**:
  - *Fixed debounce (e.g. 300 ms after the last frame)* — simpler to write, but see above: does not
    adapt to backend latency, and either drops frames unnecessarily (backend is fast) or overlaps
    requests unnecessarily (backend is slow).
  - *`AbortController` per batch, cancel-and-restart on every frame* — correct, but wastes every
    in-flight fetch instead of letting the most recent one complete and simply skipping intermediate
    frames; more code for a mechanism the generation counter already provides for free.

## R6 — The existing unconditional 60 s poll becomes a bug the moment markers are replay-aware

- **Decision**: Gate the `setInterval(loadRulesetMarkers, 60_000)` tick
  (`index.html:932-933`) so it only calls the **live** `loadRulesetMarkers()` (no `at_time`/`forecast`)
  when `_tnLive` is true; it is a no-op otherwise. The replay-driven refresh (R5) is a separate call
  path triggered from `onFrame`, not from this interval.
- **Rationale**: Not named as its own FR, but implied by FR-001 ("ruleset marker decisions are
  computed for the currently displayed timestamp instead of 'now'" — **always**, not "except once a
  minute"). Today the interval is harmless because nothing the map shows depends on time-nav state.
  The moment `loadRulesetMarkers` becomes replay-aware, this same interval — left unconditional — would
  silently flip every marker back to the **live** decision every 60 seconds while the pilot is scrubbed
  to a past or future hour, directly contradicting what FR-001 just fixed. This is the same shape of
  discovery as spec 003's `clone_ruleset` `site_type` bug and this spec's own FR-004: a line that looks
  correct today only because nothing exercises the interaction yet.
- **Alternatives considered**:
  - *`clearInterval`/re-`setInterval` on every time-nav transition* — works, but is more moving parts
    (tracking the interval handle across `tnGoLive`/`tnSelectDay`/`tnSelectCustomDate`/`tnStartPlay`)
    for the same outcome as one `if (_tnLive)` guard at the top of the tick callback.

## R7 — Does the frontend need `_mapReplay` to expose its own `isLive()`?

> **Correction, recorded during implementation**: this entry originally concluded no cross-file
> bridging was needed because `loadRulesetMarkers` and `_tnLive` "are all already defined in the same
> file." That was wrong at the *script-tag* level, not just the file level — see below. The decision
> (no `ReplayEngine.isLive()` getter) still holds; the reasoning about where the boundary actually
> falls did not.

- **Decision**: No `ReplayEngine.isLive()` getter. But `index.html` turns out to hold **two separate
  top-level scopes**, not one: the time-nav bar / `_mapReplay` construction lives in a classic
  `<script>` block (`index.html:306-572`), while `loadRulesetMarkers` and the ruleset marker layer live
  in a later `<script type="module">` block (`index.html:573-938`). `let`/`const` bindings do not cross
  either a classic-script-to-classic-script boundary or a classic-to-module boundary — `_tnLive` and
  `_mapReplay` are invisible to the module script, and `loadRulesetMarkers` is invisible to the classic
  script. Neither script exposes anything onto `window` today. The fix actually implemented: two
  narrow, explicit `window`-exposed functions bridge the gap — `window._lentiIsTimeNavLive()` (classic
  → module, read `_tnLive` for the poll gate, R6) and `window._lentiScheduleRulesetRefresh(ts,
  isForecast)` (module → classic, so `onFrame` and `tnGoLive()` can trigger a refresh without the
  coalescing logic living in the classic script).
- **Rationale**: A full `ReplayEngine.isLive()` getter would still be the wrong shape — `ReplayEngine`
  genuinely doesn't know about time-nav's day-offset concept (`_tnLive` can be true while `_mapReplay`
  still holds loaded frames from a previous selection). The two window-exposed functions are the
  minimal bridge for the two facts that actually need to cross the boundary, not a general-purpose API.
- **Alternatives considered**: *Move `loadRulesetMarkers` into the classic script* — would remove the
  boundary entirely, but the classic/module split predates this feature and serves the rest of the
  module script (i18n, other marker layers); moving one function out for this feature's sake is a
  larger, riskier diff than two one-line `window.` bridges.

## R8 — Is `query_forecast_snapshot_for_stations`'s source-selection heuristic good enough to reuse as-is?

- **Decision**: Reuse it unchanged. It picks, per station, whichever record in the ±30 min window
  has the most fields (`influx.py:324-326`, "keep the entry with the most fields (newest init_time
  written last)") rather than the explicit swissmeteo-preferred-if-≤24h-old rule
  `query_forecast_for_stations` uses. This is a real difference, but not one this feature introduces
  or is positioned to fix.
- **Rationale**: The function is already in production use for föhn forecast evaluation
  (`routers/foehn.py:193`) with this exact heuristic. Aligning its source-preference logic with
  `query_forecast_for_stations` would be a change to shared forecast-query behaviour affecting föhn
  too — out of scope for a feature whose FR-007/NFR-004 explicitly forbid evaluation-logic changes
  beyond "which timestamp is fed in." Flagged here, not fixed, the same way spec 003 flagged
  `clone_ruleset`'s `site_type` bug without fixing it.
- **Alternatives considered**: *Fix the heuristic while touching this file* — rejected; it would
  silently change föhn's forecast evaluation too, an unrelated blast radius for this feature to own.

## R9 — Does the new forecast-at-time path need `virtual_members` handling?

- **Decision**: No. `run_forecast_evaluation_at()` takes no `virtual_members` parameter, matching
  `run_forecast_evaluation` exactly (`evaluator.py:625-631`, no such parameter either).
- **Rationale**: Forecast data is keyed by real `station_id` in `weather_forecast` — there is no
  virtual-station dedup concern for forecast conditions today (dedup only applies to `weather_data`,
  per `services/dedup.py`), and the existing full-series forecast path already omits it. Consistency
  with the sibling function, not a new decision.
