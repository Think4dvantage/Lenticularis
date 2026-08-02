# Research: Viewport-First Progressive Loading & Geolocation Centering

**Feature**: [spec.md](./spec.md)
**Phase**: 0 — Research
**Date**: 2026-08-01

Unknowns identified in the spec (and its checklist's carried-forward notes) are resolved here before
design. The single biggest finding: **most of FR-002/FR-003's "day-offset progression" already
exists** — this changes the shape of the whole plan from "build a priority-queue fetch scheduler"
to "add a viewport concept and split rendering into two priority tiers."

---

## R1 — Does the day-offset progressive-load sequence need to be built, or does it already exist?

- **Finding**: It already exists, in two places, doing exactly FR-003's alternating order:
  - Client: `index.html:567-580` — `window._stationsReady.then(...)` loops
    `for (const offset of [1, 0, 2, -1, 3, -2, 4, -3, 5])`, calling `_mapReplay.prefetch(params,
    _prefetchAbort.signal)` sequentially, cancelable via the existing `_prefetchAbort`
    `AbortController`.
  - Server: `stations.py:180-233` (`warm_replay_cache`) — the **identical** `[1, 0, 2, -1, 3, -2, 4,
    -3, 5]` sequence, run once at startup, populating the shared 5 min `_replay_cache` so the first
    real user of the day never hits a cold InfluxDB query.
  Both were already written to satisfy "tomorrow first — most commonly clicked" ordering, before this
  spec existed.
- **Decision**: Reuse both, unchanged. This spec does not introduce a new fetch-priority queue for
  day offsets — FR-003/FR-004's ordering requirement is already implemented. What genuinely does not
  exist yet, confirmed by the checklist: a **viewport (bounds) concept** anywhere in the frontend, and
  any distinction between "visible" and "off-screen" stations at all.
- **Consequence**: the real new engineering surface is narrower than the spec's framing initially
  suggests — see R2.

## R2 — What does "viewport-first" actually mean, given both station endpoints already return ALL stations in one atomic payload?

- **Finding**: `GET /api/stations` (live) and `GET /api/stations/replay` (per day-offset) both fetch
  and return **every** station in a single response — there is no per-station or bounding-box filter
  on either endpoint today (confirmed by the checklist: `list_stations()` takes only `request:
  Request`, no query params despite `architecture.md` documenting `?network=&canton=` that isn't
  actually implemented — a separate stale-docs issue, not fixed here, out of this feature's scope).
  Since a day's data always arrives as one bundle for every station, "load the visible subset first"
  cannot mean "fetch the visible subset first" without fragmenting the request — and fragmenting it
  is the wrong move (R3). It has to mean **render the visible subset first** — the map/DOM
  work of placing markers, not the network fetch.
- **Decision**: Viewport-first loading is implemented as a **two-tier render split**, not a
  fetch-level priority queue:
  1. On any point where station markers are (re)placed — initial `loadStations()`, the 60 s live
     refresh, and each replay frame's `applyReplaySnapshot()` — partition the already-fetched station
     list into **visible** (inside `map.getBounds()`) and **off-screen**.
  2. Place visible-tier markers on the map **synchronously, immediately**.
  3. Place off-screen-tier markers in a **deferred, cancelable, chunked** low-priority pass
     (`requestIdleCallback`, `setTimeout(...,0)` fallback for Safari) right after.
  This satisfies P1/NFR-001 ("visible stations show current data... without waiting on off-screen
  stations") because the *paint* — which is what "waiting" is actually observable as, once the
  network round-trip for a small "low hundreds" station count is already fast (spec's own
  Assumptions) — happens for the visible tier first, every time.
- **Alternatives considered**:
  - *Bounding-box query param on both endpoints, fetch only visible stations first, then the rest* —
    this is the literal reading of "load visible first," but see R3 for why it was rejected: it
    fragments the one shared, cacheable "all stations" query into an unbounded number of
    viewport-specific queries, actively working against NFR-002/NFR-003/the existing cache
    architecture instead of extending it (contradicting FR-006's explicit instruction).
  - *Do nothing extra for rendering; rely on the fetch already being "fast enough"* — rejected because
    marker placement itself (SVG icon string construction in `markerIcon()`/`foehnMarkerIcon()` × up
    to hundreds of markers, plus Leaflet's own DOM work) is exactly the kind of main-thread cost that
    a two-tier split defers without needing a second network round-trip.

## R3 — Why not add a `station_ids`/bbox query parameter to `/api/stations/replay`?

- **Decision**: Rejected. Keep both station endpoints exactly as they are — no new query parameters,
  no InfluxDB filter changes.
- **Rationale**: `_replay_cache` (`stations.py:69`, 5 min TTL, 256 entries) is keyed by the request's
  query-string and is **shared across every pilot** looking at the same day-offset — that sharing is
  precisely what makes `warm_replay_cache`'s startup warm-up valuable (one InfluxDB query serves every
  user of that day, not one per user). A viewport-scoped `station_ids` parameter would make the cache
  key a function of each pilot's individual pan position — effectively unbounded key cardinality (any
  two pilots panned even slightly differently would miss each other's cache entries), which:
  - Actively **violates FR-006** ("extend the existing cache-lifetime approach; don't invent an
    unrelated third TTL" — fragmenting the key space is not "extending," it breaks the one thing that
    made the shared cache work).
  - Risks **violating NFR-002** ("aggregate requests... must not exceed today's baseline") the moment
    two pilots' viewports differ even slightly, since each unique viewport now costs its own InfluxDB
    query instead of sharing the one warmed entry.
  - Directly contradicts **NFR-003**'s spirit (a *new* per-viewport server cache dimension would need
    its *own* size/TTL bound, and picking one that both (a) usefully caches per-viewport data and (b)
    doesn't explode is a much harder, riskier problem than the feature is worth).
- **Alternatives considered**:
  - *Two-phase fetch: scoped-to-viewport InfluxDB query for instant partial data, silently upgraded to
    the full shared-cache entry once it lands* — genuinely would deliver both the perceived-speed win
    and a real InfluxDB cost reduction, but is a materially bigger, riskier change (a second query
    shape to maintain, a client-side "upgrade in place" merge step) for a feature whose own
    Assumptions section already says station-count scale is "unspecified, plausibly low hundreds" —
    not yet a scale where the InfluxDB query itself (as opposed to marker rendering) is the bottleneck.
    Flagged as a candidate follow-up if station count grows materially (see Plan's Risk table).

## R4 — Does this feature need a new client-side cache, for NFR-003's bound to apply to?

- **Decision**: No new cache. `window._lentiStationsMap` (map.js, rebuilt each `loadStations()` call)
  and `ReplayEngine._cache` (`replay.js:40`, keyed by day-offset URL) already hold every station's
  data for "now" and for each day-offset respectively — both are reused unchanged. There is no
  per-viewport dimension added to either (R3), so neither cache's key space grows because of this
  feature.
- **Consequence**: NFR-003 ("the new client-side station/day cache has a size or TTL bound") is
  satisfied vacuously — there is no *new* cache to bound. Noted for completeness: `ReplayEngine._cache`
  itself has no explicit eviction today, only a TTL check on read (stale entries are never deleted,
  just treated as a miss) — in practice bounded anyway, since its key space is the ~9 day-offset URLs
  the time-nav bar exposes for a given "today," which doesn't grow within a session. Pre-existing,
  unrelated to this feature, not fixed here.

## R5 — Re-prioritization on pan/zoom (FR-005)

- **Decision**: Bind a single `map.on('moveend', ...)` listener (Leaflet fires `moveend` after both
  pans and zooms settle — no separate `zoomend` binding needed). On each `moveend`: recompute the
  visible set against the **current in-memory** station list (`window._lentiStationsMap` for live, or
  the current replay frame's snapshot) and immediately place any visible-tier stations not yet
  rendered. If a low-priority off-screen render pass is still pending from a previous split, bump its
  generation counter so the stale pass discards its remaining chunk instead of continuing to place
  markers for a viewport the pilot has already panned away from.
- **Rationale**: Because every station's data for the current day/live moment is already client-side
  the instant the (single, shared, unscoped) fetch completes (R2/R3), "re-prioritizing" on pan/zoom
  never needs a new network request — it is a pure, synchronous re-render using data already in hand.
  This is simpler and safer than a real fetch-cancellation mechanism (no `AbortController` needed
  here — nothing is in flight to abort) and mirrors the generation-counter discard pattern already
  used for exactly this kind of "supersede stale in-progress work" problem in
  `specs/007-replay-aware-ruleset-decisions` (`_rulesetMarkerGen`), reused here for a render pass
  instead of a fetch.
- **Alternatives considered**: *`_prefetchAbort`-style `AbortController` for the deferred render pass*
  — wrong tool: `AbortController` cancels in-flight I/O; the deferred pass is synchronous DOM work
  chunked across idle callbacks, which a simple generation-counter check between chunks cancels just
  as effectively with less machinery.

## R6 — Geolocation: blocking vs. non-blocking first paint (NFR-004)

- **Decision**: The map is constructed at its default center (Interlaken, zoom 11) exactly as today,
  synchronously, unconditionally (`map.js:12-16`, unchanged). Geolocation resolution is kicked off
  **after** that construction, asynchronously, and only calls `map.setView([lat, lon], 11)` if/when it
  resolves. Nothing waits on it.
- **Rationale**: This is the only way to satisfy NFR-004 literally — the permission dialog and
  position resolution can take an arbitrary, pilot-controlled amount of time (the browser's own
  permission prompt has no timeout), so first paint must never be gated on it. `map.setView(...)`
  firing later naturally triggers `moveend` (R5), which re-runs the visible-subset split for the new
  center — this is exactly FR-106's "re-triggers the visible-subset progression for the new center,"
  achieved for free by the same listener R5 already needs.
- **Alternatives considered**: *Delay `loadStations()`'s first call until geolocation settles (or a
  short timeout elapses)* — would reduce a redundant first-paint-at-Interlaken-then-recentered flash
  for pilots who grant access, but directly violates NFR-004 and P1's "without waiting."

## R7 — Persisted geolocation preference: what triggers a stored "declined," and what doesn't

- **Decision**: `localStorage` key (e.g. `lenti_geo_pref`), values `"granted"` / `"declined"` / absent
  (undecided — first visit). Only an explicit `PERMISSION_DENIED` error from
  `getCurrentPosition`'s error callback writes `"declined"`. A success writes `"granted"`. A
  `TIMEOUT`/`POSITION_UNAVAILABLE` error, or the API being entirely absent
  (`!('geolocation' in navigator)`), writes **nothing** — the stored preference (if any) is left
  exactly as it was.
- **Rationale**: this is what the edge cases require verbatim: "Permission granted but resolution
  times out or errors for one session → falls back to Interlaken for that session only; the
  remembered 'granted' preference is not permanently revoked by a transient failure — it retries next
  visit," and "Geolocation API unavailable... treated as decline" for *behaviour* (fall back silently,
  no prompt-equivalent, no error surfaced) without necessarily writing a permanent `"declined"` that
  would block a retry if the pilot later switches to a secure/supporting browser — since checking
  `'geolocation' in navigator` costs nothing and never shows a permission dialog, there is no "nagging"
  risk in re-checking it every visit; only an actual denied *prompt* needs to be remembered so the
  prompt itself doesn't reappear (FR-104).
- **Consequence**: On every load where the stored preference is not `"declined"`, geolocation is
  attempted (first visit *or* a previously-granted visit *or* a previously-timed-out visit) — matching
  FR-104's "a prior grant auto-recenters... on every subsequent visit" and the edge case's "retries
  next visit" for transient failures.

## R8 — Total station count, for sizing decisions

- **Finding**: No authoritative count is documented anywhere in `.ai/context/` (the checklist already
  flagged this). Given R3/R4 (no new cache, no new query), no design decision in this plan actually
  depends on a precise number — "unspecified, plausibly low hundreds" (the spec's own Assumption)
  remains the working scale estimate. Not blocking.

## R9 — HTTPS / secure-context assumption

- **Finding**: `navigator.geolocation.getCurrentPosition` requires a secure context in all modern
  browsers; an insecure context makes the call fail immediately via the error callback (commonly
  `POSITION_UNAVAILABLE`), which R7's design already treats as "fall back silently, don't persist a
  decline." The production deployment is behind Traefik with the `lg4.ch` domains (per
  `architecture.md`'s deployment section) — assumed HTTPS-terminated there; a local plain-HTTP dev
  server would simply see geolocation silently fail every visit, which is the correct, harmless
  degradation per NFR-004/edge cases, not a bug to special-case.
