# Feature: Viewport-First Progressive Loading & Geolocation Centering

**Created**: 2026-08-01
**Status**: Drafted — 0 open questions, ready for `plan.md`
**Next step**: `plan.md`

## Overview

Two related changes to how the map first shows data, bundled together because the second changes
what "on screen" even means for the first.

**Today's loading is all-or-nothing.** `loadStations()` (`static/map.js:372-410`) fetches
`GET /api/stations` — every station, bundled with its latest reading, in one payload — and repeats
that full fetch every 60 s (`startLiveRefresh`, `map.js:422-425`), regardless of what's actually
visible on screen. The time-nav/replay path is the same shape: `ReplayEngine` (`static/replay.js`)
and `GET /api/stations/replay` (`stations.py:367-374`) always fetch **all** stations for the
selected day/window; neither the client nor the server has any concept of "only the stations in the
current viewport" — there is no `getBounds()`/bounding-box logic anywhere in the frontend today.
As the station count grows, this means every map load and every day-offset click pays for data the
pilot isn't looking at.

**Today's map center is fixed.** The map always opens centered on Interlaken at zoom 11
(`L.map('map', { center: [46.6863, 7.8632], zoom: 11, ... })`, `map.js:12-16`). There is no
geolocation code anywhere in the codebase (`navigator.geolocation` has zero references) and no
"center on my location" control.

This feature makes initial and ongoing loading **viewport-first and time-progressive** — load what's
on screen, starting with now, before anything else — and makes the *viewport itself* location-aware,
so a pilot near their own site sees their own stations load first instead of Interlaken's.

## User Stories

### P1 — On-screen stations load first, starting with now

As a pilot opening the map, I want the stations currently visible on screen to show their **current**
data immediately, without waiting on data for stations I can't see or for other days, so the map feels
fast regardless of how many stations Lenticularis tracks in total.

**Acceptance Criteria**:
- Opening the map (or panning/zooming to a new area) shows live data for the newly-visible stations
  without waiting on off-screen stations or other days.
- No behaviour changes for a pilot who never pans or changes day — they still see exactly what they
  see today, just sooner.

### P2 — Other days load next, for the same visible stations, oldest-need-first

As a pilot who might check tomorrow's forecast or yesterday's read after glancing at now, I want the
next few likely-useful days to already be loading in the background for my visible stations, in the
order I'm most likely to want them, so clicking a day button rarely means waiting.

**Acceptance Criteria**:
- After "now" loads for the visible stations, Lenticularis continues loading, for those same
  stations: tomorrow, yesterday, the day after tomorrow, the day before yesterday, and so on —
  alternating outward — through the existing day-offset range already exposed by the time-nav bar
  (today's buttons cover -3..+5).
- Clicking a day button that has already progressively loaded shows data immediately from cache
  instead of triggering a fresh wait.

### P3 — Off-screen stations warm the same way, just deprioritized

As a pilot who then pans to a new area, I want stations that were off-screen to already have some data
cached using the same now/tomorrow/yesterday/... pattern, so panning around doesn't feel like
starting over each time.

**Acceptance Criteria**:
- Once the visible-station progression (P1+P2) is underway, Lenticularis begins the identical
  now → +1 → -1 → +2 → -2 → ... progression for stations **not** currently on screen, at lower
  priority.
- Panning to reveal a station that has already been warmed this way shows data with no visible delay.
- Panning to reveal a station that hasn't been warmed yet still loads on demand — it never gets stuck
  showing nothing.

### P4 — First-time location access centers the map on the pilot, not Interlaken

As a pilot opening Lenticularis for the first time, I want to be asked for location access and, if I
grant it, have the map open centered on where I actually am (at today's standard zoom) instead of
Interlaken, so the stations that load first (per P1) are the ones near me.

**Acceptance Criteria**:
- On first visit (no stored preference yet), the browser's location-permission prompt is triggered.
- If granted, the initial view centers on the resolved coordinates at zoom 11 — the same zoom
  Interlaken uses today — instead of Interlaken.
- The grant is remembered: future visits recenter on the pilot's (freshly re-resolved) location
  automatically, without asking again.

### P5 — Declining location access changes nothing

As a pilot who denies location access, or whose browser doesn't support it, I want the map to behave
exactly as it does today, with no broken UI and no repeated nagging every visit.

**Acceptance Criteria**:
- Denial, dismissal, or an unsupported/unavailable geolocation API all leave the map at today's
  Interlaken/zoom-11 default.
- The decision is remembered so the app itself doesn't keep re-prompting on every subsequent load.

### P6 — Changing your mind later

As a pilot who declined at first but wants to switch on location centering later, I want an explicit,
always-visible control to try again, since nothing else can bring the browser's own prompt back.

**Acceptance Criteria**:
- A visible control (e.g. a "center on my location" icon) re-attempts geolocation at any time,
  independent of the stored first-visit outcome.
- Succeeding this way updates the remembered preference the same as a first-visit grant would.

## Functional Requirements

### Viewport-first progressive loading

- **FR-001**: On initial map load, on pan/zoom, and on the periodic live refresh, Lenticularis
  determines which stations fall inside the current viewport and prioritizes that subset.
- **FR-002**: The first data loaded for the visible subset is "now" (live) data — matching what the
  map shows today, just scoped to what's visible.
- **FR-003**: After "now," Lenticularis progressively loads additional days for the same visible
  subset in this fixed, alternating order: +1 (tomorrow), -1 (yesterday), +2, -2, ... continuing
  through the day-offset range the time-nav bar already exposes (-3..+5) — not beyond it.
- **FR-004**: Once the visible-subset progression (FR-002+FR-003) is underway, Lenticularis begins
  the identical now → +1 → -1 → +2 → -2 → ... progression for stations **not** currently in the
  viewport, at lower priority, populating a client-side cache.
- **FR-005**: Panning, zooming, or changing the day offset while a progressive load is in flight
  re-prioritizes the queue: the new viewport's "now" step jumps to the front, superseding
  lower-priority work in progress rather than waiting for it to finish first.
- **FR-006**: Data already fetched for a given (station, day-offset) combination is served from cache
  rather than re-fetched, following the existing cache-lifetime approach (extend it; don't invent an
  unrelated third TTL alongside the existing 5 min server-side / 10 min client-side replay caches).
- **FR-007**: A station whose data hasn't been prefetched yet (visible or not) still loads on demand
  the moment it's needed — the progressive order is a priority, not a hard gate.
- **FR-008**: The live 60 s auto-refresh applies to the currently visible stations, not unconditionally
  to the full station list.

### Geolocation centering

- **FR-101**: On first visit (no stored location preference), Lenticularis requests browser
  geolocation permission as part of the initial map load.
- **FR-102**: If granted, the initial view centers on the resolved coordinates at zoom 11 (today's
  Interlaken zoom) rather than Interlaken's coordinates.
- **FR-103**: If denied, dismissed, unsupported, or resolution fails, the initial view is Interlaken
  at zoom 11 — unchanged from today.
- **FR-104**: The grant/deny outcome is remembered client-side: a prior grant auto-recenters on a
  freshly re-resolved location on every subsequent visit without re-prompting; a prior denial does
  not auto-trigger the prompt again.
- **FR-105**: An explicit, always-available UI control lets the pilot (re-)attempt geolocation at any
  time regardless of the stored outcome.
- **FR-106**: The viewport used to decide "stations shown" for FR-001 is the resolved center —
  Interlaken by default, or the pilot's location once resolved — so a location that resolves after
  the very first paint re-triggers the visible-subset progression for the new center.
- **FR-107**: Any new user-facing copy (permission prompt context, the "center on my location"
  control) is localized in all four supported locales, per existing i18n convention.

## Non-Functional Requirements

- **NFR-001 (Perceived load time)**: initial paint shows visible-station "now" data without waiting
  on off-screen stations or other days.
- **NFR-002 (Backend load)**: aggregate requests/bytes transferred in a typical session (open the
  map, look around, check a couple of days) must not exceed today's baseline of "fetch everything,
  always, every 60 s." Background prefetch is paced, not fired as an immediate burst.
- **NFR-003 (Bounded client cache)**: the new client-side station/day cache has a size or TTL bound —
  mirrors the existing "no unbounded in-memory caches" rule (`04-constraints.md`), applied
  client-side this time.
- **NFR-004 (No blocking on geolocation)**: the map's initial paint is never blocked waiting on the
  permission dialog or position resolution — a usable map (Interlaken default) appears immediately,
  and recenters if/when a location resolves.
- **NFR-005 (Privacy-respecting)**: only the coarse lat/lon needed to pick a map center is
  requested/stored; nothing from this feature is sent to Lenticularis' backend unless `plan.md` finds
  a concrete reason it must be.
- **NFR-006 (No per-station backend loops)**: any new viewport-scoped batch query follows the
  existing "no per-station Influx loops" rule (`04-constraints.md`) — one batched query for the
  requested station set, not one query per station.

## Success Criteria

- Opening the map shows "now" data for on-screen stations without waiting on off-screen or
  other-day data.
- Clicking a day button that has already progressively loaded (per FR-003) shows data with no visible
  delay; one that hasn't still loads correctly, just not instantly.
- Panning to a previously off-screen, already-warmed area shows data immediately.
- A typical session's total requests/bytes are lower than or equal to today's "always fetch
  everything" baseline.
- A first-time pilot who grants location access sees the map centered on their own location at zoom
  11, with their nearby stations loading first — without navigating there manually.
- A pilot who denies access sees exactly today's Interlaken default, with no repeated prompts on
  later visits, and can still switch it on later via the explicit control (FR-105).

## Key Entities

| Entity | Notes |
|---|---|
| Viewport-visible station set | Stations whose lat/lon fall within the map's current bounds; recomputed on pan/zoom/geolocation resolve. |
| Load priority queue | Ordered (visibility, then \|day-offset\| ascending, alternating future/past) per FR-003/FR-004; re-prioritized per FR-005. |
| Client-side station/day cache | Keyed by (station, day-offset); bounded per NFR-003; reuses/extends the existing replay caching pattern rather than adding an unrelated third one. |
| Location preference | Client-stored outcome of the geolocation prompt (granted / declined) plus, when granted, the last resolved position — drives FR-104. |

## Out of Scope

- The exact API shape for viewport-scoped fetching (a new bounding-box query parameter vs. reusing
  the existing full station-metadata list client-side and only scoping the *data* requests) is a
  `plan.md` decision, not fixed here.
- Continuous location tracking (`watchPosition`). One resolution per visit is sufficient — the map
  does not follow the pilot's live movement after centering.
- Reverse-geocoding the resolved coordinates into a place name/label.
- Changing the time-nav bar's own day-offset range (-3..+5), the replay/Play animation mechanics, or
  ruleset marker evaluation timing (`specs/007-replay-aware-ruleset-decisions`) — this spec is about
  which stations' *weather data* loads and in what order, not when ruleset decisions are recomputed.
  The two features are complementary, not conflicting, but both touch `_mapReplay`, `index.html`'s
  day/hour handlers, and `map.js` — see Dependencies.
- Any server-side account/profile storage of the location preference — client-side storage covers
  both authenticated and anonymous visitors of the same map page without a login requirement.

## Assumptions

- The live `/api/stations` payload's per-station cost is unconfirmed and likely small (no documented
  total station count — treat scale as "unspecified, plausibly low hundreds"); the clearest
  performance win is on the replay/historical-forecast path, which runs heavier InfluxDB range
  queries per day-offset across all stations regardless of viewport. The same viewport-first
  architecture is applied uniformly to both for consistency and to front-run future station growth.
- Geolocation requires a secure context (HTTPS); Lenticularis' deployment is assumed to already serve
  over HTTPS — `plan.md` should confirm.
- Client-side storage (e.g. `localStorage`) is sufficient for the location preference; no server
  round-trip is required.

## Dependencies

- Introducing `map.getBounds()`/marker-in-bounds logic — does not exist in the frontend today.
- The existing day-offset range (-3..+5) already surfaced by the time-nav bar bounds how far the
  alternating progression (FR-003/FR-004) goes.
- The existing server-side (5 min TTL, `_TTLCache`) and client-side (10 min TTL, `ReplayEngine`)
  replay caches — this feature extends that pattern rather than introducing an unrelated third cache
  lifetime (FR-006, NFR-003).
- Browser Geolocation API (`navigator.geolocation`).
- The i18n framework (4 locale files) for any new prompt/control copy (FR-107).
- **Cross-cutting with `specs/007-replay-aware-ruleset-decisions`**: both specs touch `_mapReplay`,
  `loadStations`/`loadRulesetMarkers`, and `index.html`'s pan/zoom/day-offset handling. Whichever
  ships second must re-read the other's `plan.md` before finalizing, so two independent
  debounce/cancellation mechanisms don't get bolted onto the same code paths in conflicting ways.

## Edge Cases

- Station whose data hasn't been prefetched yet (visible or off-screen) is panned into view or
  requested directly → loads on demand (FR-007); never shows stuck-empty.
- Pilot pans/zooms/changes day rapidly, faster than in-flight requests resolve → latest viewport/day
  wins (FR-005); stale in-flight responses must not overwrite the current view.
- Geolocation API unavailable (non-HTTPS context, unsupported browser) → treated as decline; Interlaken
  default; no console errors surfaced to the pilot.
- Permission granted but resolution times out or errors for one session → falls back to Interlaken for
  that session only; the remembered "granted" preference is not permanently revoked by a transient
  failure — it retries next visit.
- Pilot revokes location permission at the OS/browser level after previously granting → the next
  resolution attempt fails silently; treated the same as a fresh decline, not an error.
- Resolved location is far outside any station's coverage (e.g. pilot traveling abroad) → map still
  centers there at zoom 11; showing few or no stations is expected, not a bug.
- Preference is per-browser (client-side), not account-wide — granting in one browser/profile has no
  effect on another.
