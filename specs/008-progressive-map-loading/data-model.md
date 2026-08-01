# Data Model: Viewport-First Progressive Loading & Geolocation Centering

**Feature**: [spec.md](./spec.md) · **Research**: [research.md](./research.md)
**Phase**: 1 — Design

---

## Summary

**No SQLite table changes. No InfluxDB measurement/tag/field changes. No API route or query
parameter changes.** (research R2, R3). Everything in this feature is client-side: a viewport-bounds
check, a two-tier render split applied at existing marker-placement call sites, and a
`localStorage`-backed geolocation preference. No new client-side cache is introduced (research R4) —
`window._lentiStationsMap` and `ReplayEngine._cache` are reused exactly as they are today.

---

## New: viewport partitioning (`static/map.js`)

```js
function _partitionByViewport(stations) {
  const bounds = map.getBounds();
  const visible = [], offscreen = [];
  for (const s of stations) {
    if (s.latitude == null || s.longitude == null) continue;
    (bounds.contains([s.latitude, s.longitude]) ? visible : offscreen).push(s);
  }
  return { visible, offscreen };
}
```

Used by every place that currently places station markers in one pass: `loadStations()`,
`applyReplaySnapshot()`. Ruleset markers (`rulesetLayer`/`loadRulesetMarkers`, specs/007's territory)
are untouched — this feature is scoped to weather-station markers only, per the spec's own Key
Entities table (all rows are about "stations").

## New: two-tier deferred render (`static/map.js`)

> **Refined during implementation**: the sketch below originally threaded the offscreen list through
> `_deferChunked`'s recursion as a parameter. The shipped version instead keeps it as a single shared
> module-level `_pendingOffscreen` array. That change is required, not cosmetic — see the `moveend`
> handler below, which needs to remove items from the *same* pending queue the chunker is draining,
> so a station is never placed twice (once by a pan/zoom promotion, once by the chunker catching up
> to it).

```js
let _renderGen = 0;              // bumped on every new full render pass (new fetch/frame)
let _pendingOffscreen = [];       // stations not yet placed — shared with the moveend handler

function _renderStationsViewportFirst(stations, placeFn) {
  const gen = ++_renderGen;
  const { visible, offscreen } = _partitionByViewport(stations);
  visible.forEach(placeFn);                 // synchronous — paints immediately
  _pendingOffscreen = offscreen;
  _deferChunked(placeFn, gen);               // low priority — see below
}

function _deferChunked(placeFn, gen, chunkSize = 40) {
  if (_pendingOffscreen.length === 0) return;
  const schedule = window.requestIdleCallback || (cb => setTimeout(cb, 0));
  schedule(() => {
    if (gen !== _renderGen) return; // superseded by a newer full render pass
    const chunk = _pendingOffscreen.splice(0, chunkSize);
    chunk.forEach(placeFn);
    _deferChunked(placeFn, gen, chunkSize);
  });
}
```

- `placeFn(station)` is the existing per-station marker-construction-and-add logic already inline in
  `loadStations()`/`applyReplaySnapshot()` — extracted into a shared function, not duplicated three
  ways (`markerIcon`/`foehnMarkerIcon` selection, `personalLayer` vs `markerLayer` routing, popup
  bind).
- `_renderGen` is the same "discard superseded work" shape as specs/007's `_rulesetMarkerGen`, applied
  here to a *render* pass rather than a *fetch* (research R5) — no `AbortController` needed since
  nothing is in flight to cancel, only a chunked loop to stop early.
- `chunkSize=40` is a starting point, not a tuned constant — the point is "don't block the main thread
  for one long synchronous loop over off-screen stations," not a specific throughput target.

### `moveend` handler — re-prioritization (FR-005)

```js
map.on('moveend', () => {
  if (_pendingOffscreen.length === 0) return; // nothing left to promote
  const bounds = map.getBounds();
  const stillPending = [];
  for (const s of _pendingOffscreen) {
    if (bounds.contains([s.latitude, s.longitude])) _placeStationMarker(s);
    else stillPending.push(s);
  }
  _pendingOffscreen = stillPending;
});
```

This promotes newly-visible stations **out of** the still-pending queue and places them immediately —
it deliberately does **not** call `_renderStationsViewportFirst` again, which would re-partition the
*entire* original station list and re-place stations already sitting on the map, creating duplicate
markers. Already-placed markers are simply left alone; only what's still waiting in
`_pendingOffscreen` is ever reconsidered (research R2/R5: the data is already client-side, so no new
fetch is needed either way).

---

## New: geolocation preference (`localStorage`)

| Key | Values | Written when |
|---|---|---|
| `lenti_geo_pref` | `"granted"` \| `"declined"` \| *(absent)* | `"granted"` on any successful `getCurrentPosition`; `"declined"` **only** on an explicit `PERMISSION_DENIED` error. A `TIMEOUT`/`POSITION_UNAVAILABLE` error or an absent `navigator.geolocation` writes nothing (research R7) |

No server round-trip (NFR-005) — this key never leaves the browser.

```js
function _tryGeolocate(onSettle) {
  if (!('geolocation' in navigator)) { onSettle(null); return; }
  navigator.geolocation.getCurrentPosition(
    pos => {
      localStorage.setItem('lenti_geo_pref', 'granted');
      onSettle({ lat: pos.coords.latitude, lon: pos.coords.longitude });
    },
    err => {
      if (err.code === err.PERMISSION_DENIED) localStorage.setItem('lenti_geo_pref', 'declined');
      onSettle(null);
    },
    { timeout: 8000 }
  );
}

// Map already constructed at Interlaken/zoom 11 above this point — never blocked on the below.
if (localStorage.getItem('lenti_geo_pref') !== 'declined') {
  _tryGeolocate(pos => { if (pos) map.setView([pos.lat, pos.lon], 11); });
}
```

`map.setView(...)` firing later triggers the existing `moveend` handler above, which re-runs the
viewport split for the new center — satisfying FR-106 without any extra wiring.

### Explicit "center on me" control (FR-105/P6)

A Leaflet custom control (same shape as the existing `_PersonalToggle` control, `map.js:323-363`)
that calls `_tryGeolocate` **unconditionally** — ignoring the stored preference — and on success calls
`map.setView(...)`. Available regardless of prior grant/decline, satisfying P6 ("changing your mind
later").

---

## What is NOT changing

- `GET /api/stations`, `GET /api/stations/replay` — no new query parameters, no response-shape change
  (research R3).
- `_replay_cache` (server, 5 min TTL) and `warm_replay_cache`'s day-offset sequence — unchanged
  (research R1, R3).
- `ReplayEngine._cache` (client, 10 min TTL) and its day-offset URL keying — unchanged (research R4).
- `_prefetchAbort` background prefetch loop (`index.html:564-580`) — unchanged; it already implements
  FR-003/FR-004's day-offset ordering (research R1).
- Ruleset markers (`rulesetLayer`, `loadRulesetMarkers`) — untouched; out of this feature's scope.
- No SQLite/InfluxDB schema change anywhere.
