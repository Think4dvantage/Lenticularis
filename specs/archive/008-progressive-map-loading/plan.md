# Implementation Plan: Viewport-First Progressive Loading & Geolocation Centering

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md) · **Data model**: [data-model.md](./data-model.md)
**Phase**: 2 — Plan · **Date**: 2026-08-01
**Next step**: `tasks.md`

---

## Technical Context

| Concern | Choice |
|---|---|
| Scope | Weather-station markers only (`map.js`) — live view, replay frames, live 60s refresh. Ruleset markers (specs/007) untouched |
| API | **No changes.** `GET /api/stations` and `GET /api/stations/replay` keep their current shape — viewport-first is a render-order concept, not a fetch-scoping one (research R2/R3) |
| Persistence | None server-side. One new `localStorage` key, `lenti_geo_pref` |
| Frontend | Vanilla JS in `static/map.js` (viewport split, geolocation, new Leaflet control). No build step |
| Caching | No new cache. Existing server `_replay_cache` and client `ReplayEngine._cache` unchanged (research R3/R4) |
| Tests | No backend tests needed (nothing server-side changes). No frontend test harness exists yet (`06-testing-conventions.md`: "no Playwright yet") — verified manually in-browser |

**Architecture approach**: the spec's framing ("priority queue," "viewport-scoped fetching") suggested
a bigger backend-touching feature than it turns out to need. The day-offset progressive-load sequence
already exists (`_prefetchAbort` loop + `warm_replay_cache`, research R1); both station endpoints
already return every station in one atomic payload, which means "viewport-first" can only be
implemented as **render-order prioritization** on data already in hand, not fetch-order (research R2).
This keeps the feature entirely client-side, adds no new cache dimension, and cannot regress
NFR-002/NFR-003 by construction (zero new requests, zero new cache keys).

**Key dependencies**: none new (Leaflet's `getBounds()`/`LatLngBounds.contains()` and the browser
Geolocation API are both already-available platform features, no library addition).

---

## Constitution Check

Per `.ai/instructions/00-ai-usage.md`.

| # | Principle | Status | Notes |
|---|---|---|---|
| 1 | Read before acting | ✅ Pass | Found the existing day-offset prefetch loop and `warm_replay_cache` before designing a redundant one; found the shared-cache fragmentation risk before proposing a bbox API |
| 2 | Plan before building | ✅ Pass | This document. No code written |
| 3 | Minimal scope | ✅ Pass | Explicitly rejected the bigger bbox-query-param design (research R3) in favor of the smaller render-split design that satisfies the same acceptance criteria |
| 4 | Tool-agnostic instructions | ✅ Pass | Nothing outside `.ai/` |
| 5 | Keep docs in sync | ✅ Pass | Phase 4. `architecture.md` gains a short note on the viewport render-split and geolocation preference; `features.md` gains a milestone |
| 6 | No secrets committed | ✅ Pass | None involved |
| 7 | Prod is off-limits | ✅ Pass | No deployment change |

**No blocking violations.**

### Additional constraint compliance (`04-constraints.md`)

| Constraint | How this plan complies |
|---|---|
| Never add npm/build step | Vanilla JS only, same as everything else in `static/` |
| Static assets → version bump | `map.js` changes → **`pyproject.toml` must be bumped** |
| i18n — all 4 locales together | New "center on my location" control needs a translated label, added to `en`/`de`/`fr`/`it` in the same commit |
| No per-station backend loops (T09) | N/A — no backend query changes at all |
| Unbounded in-memory caches (T10) | N/A — no new cache introduced (research R4); nothing to bound |

---

## Data Model Summary

No storage changes anywhere (full detail in [data-model.md](./data-model.md)). Three client-side
additions, all in `static/map.js`:

1. `_partitionByViewport(stations)` — splits a station list into visible/off-screen using
   `map.getBounds().contains([lat,lon])`.
2. `_renderStationsViewportFirst(stations, placeFn)` + `_deferChunked(...)` — places the visible tier
   immediately, the off-screen tier via chunked `requestIdleCallback` (superseded by a generation
   counter on `moveend`/refresh/new replay frame).
3. `lenti_geo_pref` `localStorage` key + `_tryGeolocate()` — resolves a position asynchronously after
   the map is already painted at its default center; recenters via `map.setView(...)` if granted.

---

## File Structure

**Modify**

| File | Change |
|---|---|
| `static/map.js` | Extract `_placeStationMarker(s)` from the existing inline loop bodies in `loadStations()`/`applyReplaySnapshot()`; add `_partitionByViewport`, `_renderStationsViewportFirst`, `_deferChunked`, `_renderGen`; `map.on('moveend', ...)`; geolocation (`_tryGeolocate`, `lenti_geo_pref`, initial attempt, recenter-on-resolve); new `_GeolocateControl` Leaflet control (mirrors `_PersonalToggle`) |
| `static/i18n/{en,de,fr,it}.json` | New key(s) for the "center on my location" control label — all four together |
| `pyproject.toml` | **Version bump** |
| `.ai/context/architecture.md`, `.ai/context/features.md` | Doc sync |

**No new files** — this is small enough to live entirely in the existing `map.js`.

---

## Implementation Phases

### Phase 1 — Viewport partition + two-tier render (P1, P2, P3, FR-001–004, FR-007, FR-008)

1. Extract `_placeStationMarker(s)` from the duplicated marker-construction bodies currently inline in
   both `loadStations()` and `applyReplaySnapshot()` (icon selection, personal-vs-professional layer
   routing, lazy popup bind) — a pure refactor, no behaviour change, done first so the next step has
   one function to call instead of two copies to keep in sync.
2. `_partitionByViewport`, `_renderStationsViewportFirst`, `_deferChunked`, `_renderGen` (data-model.md).
3. Wire both `loadStations()` and `applyReplaySnapshot()` through
   `_renderStationsViewportFirst(stations, _placeStationMarker)` instead of their current flat
   `for`-loop.
4. Track `_lastStationsSnapshot` (the list most recently rendered) so the `moveend` handler (Phase 2)
   has something to re-render from without a new fetch.
5. FR-007 (on-demand load for a not-yet-prefetched day) needs **no new code** — `ReplayEngine.load()`'s
   existing cache-miss branch already fetches fresh on any day the background loop hasn't reached yet
   (research R1); this phase doesn't touch that path.
6. FR-008 (60s refresh prioritizes visible stations) is satisfied by `loadStations()` now going
   through the same viewport-first render on every tick — no separate refresh-specific code.

*Verifiable*: manually — open the map, confirm markers in view appear to place before markers far off
zoomed out; throttle CPU in devtools to make the effect visible if a "low hundreds" station count
renders too fast to perceive at normal speed.

### Phase 2 — Re-prioritization on pan/zoom (FR-005)

1. `map.on('moveend', ...)` — re-run `_renderStationsViewportFirst(_lastStationsSnapshot,
   _placeStationMarker)` for the new bounds. The generation-counter bump inside
   `_renderStationsViewportFirst` automatically supersedes any still-pending off-screen chunk from the
   previous viewport (research R5) — no separate cancellation code needed.
2. Test manually: pan to a new area before the off-screen tier has finished placing (throttle CPU to
   make the window visible); confirm markers for the newly-visible area appear without a full-page
   re-render glitch, and confirm no console errors from a superseded chunk trying to run.

### Phase 3 — Geolocation centering (P4–P6, FR-101–107)

1. `_tryGeolocate(onSettle)` — data-model.md's implementation. Called once after map construction,
   unconditionally unless `localStorage.getItem('lenti_geo_pref') === 'declined'`.
2. On success: `map.setView([lat, lon], 11)` — triggers Phase 2's `moveend` handler for free (FR-106).
3. New `_GeolocateControl` Leaflet control (mirrors `_PersonalToggle`'s `L.Control.extend` shape,
   `map.js:323-363`) — button always available, calls `_tryGeolocate` unconditionally regardless of
   stored preference, recenters on success (P6/FR-105).
4. i18n: one new key for the control's label (e.g. `map.geolocate_button`), all 4 locale files
   together (FR-107). Follow the existing `_PersonalToggle` pattern — `window.t()` for the visible
   label; the `title` attribute may stay a plain string like the existing toggle button does.
5. Edge cases (research R7): `PERMISSION_DENIED` → persist `"declined"`; `TIMEOUT`/
   `POSITION_UNAVAILABLE`/absent API → fall back silently, write nothing. `console.warn` (not `.error`)
   on failure, per `[Lenti:map]` logging convention — a decline/timeout is an expected outcome, not a
   bug.
6. **Bump `pyproject.toml`.**

*Verifiable*: manually, in a browser that supports geolocation over the dev HTTPS origin — grant once,
reload, confirm auto-recenter without a second prompt; deny once, reload, confirm Interlaken default
and no re-prompt; click the new control after a decline, confirm it still attempts geolocation.

### Phase 4 — Docs sync

`architecture.md` gains a short note (viewport render-split exists in `map.js`; no new cache; no new
API surface) so a future reader doesn't assume this feature added a bounding-box query parameter that
was deliberately rejected. `features.md` gains a milestone entry. Constitution #5.

---

## Dependencies

- **External**: none. `Leaflet.LatLngBounds.contains()`, `navigator.geolocation`,
  `requestIdleCallback` (with a `setTimeout` fallback for Safari, which lacks it) are all native
  platform APIs already reachable from vanilla JS.
- **Internal**: `_prefetchAbort` background loop and `warm_replay_cache` (read, not changed, research
  R1); `ReplayEngine._cache` and `_replay_cache` (read, not changed, research R3/R4).
- **Cross-cutting with `specs/007-replay-aware-ruleset-decisions`** (already shipped): both touch
  `index.html`'s time-nav/replay wiring, but at different layers — 007 changed *which timestamp*
  ruleset markers evaluate against (`loadRulesetMarkers`, the module `<script>` block); this feature
  changes *render order* of weather-station markers (`map.js`, a classic `<script>`, sharing top-level
  scope with `index.html`'s own classic `<script>` block but never touching `loadRulesetMarkers` or
  `_mapReplay`'s `onFrame` callback body). No shared code path, no conflicting debounce/cancellation
  mechanism — 007's coalescing queue guards ruleset-decision fetches; this feature's generation
  counter guards station-marker *render* passes. Verified no overlap by inspecting `onFrame`
  (`index.html:317-347`): it calls `applyReplaySnapshot` (this feature's call site) and then, since
  007, `window._lentiScheduleRulesetRefresh` (007's call site) — sequential, independent.

---

## Risk & Mitigations

| # | Risk | Severity | Mitigation |
|---|---|---|---|
| 1 | **Adding a bbox/`station_ids` query parameter anyway**, because it looks like the more literal reading of "load visible stations first" | High | R2/R3 explain why this fragments the shared replay cache; Phase 1 is written around the render-split design specifically so this isn't the path of least resistance during implementation |
| 2 | **Re-deriving a new day-offset priority sequence**, duplicating the existing `_prefetchAbort` loop / `warm_replay_cache` order | Medium | R1 documents that it already exists; Phase 1 explicitly does not touch either |
| 3 | **`moveend` re-render fighting with the deferred off-screen chunk from the previous viewport**, causing flicker or duplicate markers | Medium | Generation counter (`_renderGen`) — a stale chunk checks it and stops before placing anything for an outdated viewport; `_renderStationsViewportFirst` doesn't clear existing markers, so no flicker either way |
| 4 | **First paint blocked on the geolocation permission dialog** | High — directly named in NFR-004 | R6: map construction and the first `loadStations()` call happen unconditionally before geolocation is even attempted; `setView` only ever runs *later*, if resolved |
| 5 | **A transient geolocation error (timeout) permanently overwrites a previously-granted preference** | Medium — named in Edge Cases | R7: only `PERMISSION_DENIED` writes `"declined"`; every other failure mode writes nothing, leaving a prior `"granted"` to retry next visit |
| 6 | **Safari lacking `requestIdleCallback`** silently breaking the off-screen render pass | Low | `setTimeout(cb, 0)` fallback in `_deferChunked` |
| 7 | Static assets change without a version bump | Medium | Phase 3 step 6 bumps `pyproject.toml` |
| 8 | New i18n string added to only some locale files | Medium | Phase 3 step 4 is explicit: all four together |

## Pre-existing gaps found during planning — flagged, not fixed here

- **`list_stations()` takes no query parameters at all**, despite `architecture.md:112` documenting
  `?network=&canton=` as if they exist (the checklist already found this). Unrelated to this feature
  (which deliberately adds no query parameters to either station endpoint, research R3) — worth its
  own doc-vs-code reconciliation pass.
- **`ReplayEngine._cache` has no explicit eviction**, only a TTL check on read (research R4). Harmless
  in practice because its key space is bounded by the ~9 day-offset URLs a session ever produces, but
  it does not literally satisfy "the existing cache-lifetime approach" as a *bounded* cache the way
  `_replay_cache` (server) does. Not touched here since this feature adds no new dimension to it.
