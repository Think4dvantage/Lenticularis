/**
 * Lenticularis — map.js
 *
 * Leaflet.js map using OpenTopoMap tiles (free, no API key).
 * Fetches /api/stations, drops a marker per station, binds a popup
 * with the latest measurement and a "View history →" link.
 */

// ---------------------------------------------------------------------------
// Map init — centred on Switzerland
// ---------------------------------------------------------------------------
const map = L.map('map', {
  center: [46.6863, 7.8632],
  zoom: 11,
  zoomControl: true,
});
// Expose globally so the auth module script can add the ruleset layer
window._lentiMap = map;

L.tileLayer('https://{s}.tile.opentopomap.org/{z}/{x}/{y}.png', {
  attribution:
    'Map data: &copy; <a href="https://openstreetmap.org/copyright">OpenStreetMap</a> contributors, ' +
    '<a href="https://viewfinderpanoramas.org">SRTM</a> | ' +
    'Map style: &copy; <a href="https://opentopomap.org">OpenTopoMap</a> ' +
    '(<a href="https://creativecommons.org/licenses/by-sa/3.0/">CC-BY-SA</a>)',
  maxZoom: 17,
}).addTo(map);

// ---------------------------------------------------------------------------
// Networks considered "personal" (community/hobbyist stations).
// Markers get a visual flag and can be toggled off.
// ---------------------------------------------------------------------------
const PERSONAL_NETWORKS = new Set(['wunderground', 'ecowitt']);

// ---------------------------------------------------------------------------
// Marker colours per network (used in popups)
// ---------------------------------------------------------------------------
const NETWORK_COLOR = {
  meteoswiss: '#63b3ed',
  holfuy:     '#9ae6b4',
  slf:        '#d6bcfa',
  ecovitt:    '#fbd38d',
};

// ---------------------------------------------------------------------------
// Wind speed → colour
//  0–15 km/h : #003bc4 (blue)
// 15–30 km/h : #003bc4 → #8900c4 (blue → purple)
// 30–50 km/h : #8900c4 → #ff0000 (purple → bright red)
// 50+ km/h   : #ff0000 (bright red)
// ---------------------------------------------------------------------------
function windSpeedColor(speed) {
  if (speed == null) return '#a0aec0'; // grey = no data

  function lerp(a, b, t) { return Math.round(a + (b - a) * t); }
  function lerpRGB(c1, c2, t) {
    return `#${[0,1,2].map(i => lerp(c1[i], c2[i], t).toString(16).padStart(2,'0')).join('')}`;
  }

  const blue   = [0x00, 0x3b, 0xc4];
  const purple = [0x89, 0x00, 0xc4];
  const red    = [0xff, 0x00, 0x00];

  if (speed <= 15) return '#003bc4';
  if (speed <= 30) return lerpRGB(blue,   purple, (speed - 15) / 15);
  if (speed <= 50) return lerpRGB(purple, red,    (speed - 30) / 20);
  return '#ff0000';
}

// ---------------------------------------------------------------------------
// Marker size — scales with gust: 24 px (calm) → 52 px (70+ km/h)
// ---------------------------------------------------------------------------
function markerSize(gust) {
  const g = Math.min(Math.max(gust ?? 0, 0), 70);
  return Math.round(24 + (g / 70) * 28);
}

// ---------------------------------------------------------------------------
// Weather-condition indicator SVG helpers
// These are rendered below the main 32×32 arrow area in an extended viewBox.
// ---------------------------------------------------------------------------

// Cloud shape: three overlapping circles + white rect to flatten the base.
// yTop is the viewBox y-coordinate where the cloud begins (typically 33).
function _cloudSvg(yTop) {
  const cY = yTop + 8; // vertical centre of the cloud body
  return `<g opacity="0.92">
    <circle cx="11" cy="${cY - 1}" r="4"   fill="white" stroke="#90cdf4" stroke-width="1.2"/>
    <circle cx="17" cy="${cY - 4}" r="4.8" fill="white" stroke="#90cdf4" stroke-width="1.2"/>
    <circle cx="22" cy="${cY - 1}" r="3.5" fill="white" stroke="#90cdf4" stroke-width="1.2"/>
    <rect x="7" y="${cY}" width="18" height="5" fill="white"/>
    <line x1="7" y1="${cY + 4}" x2="25" y2="${cY + 4}" stroke="#90cdf4" stroke-width="1.2"/>
  </g>`;
}

// Three rain-drop ellipses centred at yCentre (middle drop is 2px lower).
function _dropsSvg(yCentre) {
  return `<g fill="#4299e1" opacity="0.85">
    <ellipse cx="10" cy="${yCentre}"     rx="1.8" ry="3"/>
    <ellipse cx="16" cy="${yCentre + 2}" rx="1.8" ry="3"/>
    <ellipse cx="22" cy="${yCentre}"     rx="1.8" ry="3"/>
  </g>`;
}

// ---------------------------------------------------------------------------
// Marker icon — directional arrow (tip points where wind travels TO),
// rotated by wind_direction, coloured + sized by wind_gust / wind_speed.
// Dark outline ensures visibility against the light OpenTopoMap background.
// If the station reports precipitation or near-100% humidity, weather
// condition icons are appended below the arrow in an extended SVG viewBox.
// ---------------------------------------------------------------------------
function markerIcon(station) {
  const m          = station.latest || {};
  const gust       = m.wind_gust ?? m.wind_speed;
  const color      = windSpeedColor(gust);
  const dir        = m.wind_direction ?? 0;
  const hasDir     = m.wind_direction != null;
  const size       = markerSize(gust);
  const isPersonal = PERSONAL_NETWORKS.has(station.network);
  // Personal stations get an amber center dot so they're visually distinct
  const centerFill = isPersonal ? '#f6ad55' : 'white';

  const arrow = hasDir
    ? `<g transform="rotate(${dir},16,16)">
        <line x1="16" y1="4" x2="16" y2="21"
              stroke="black" stroke-width="8" stroke-linecap="round"/>
        <polygon points="5,17 27,17 16,32" fill="black"/>
        <line x1="16" y1="4" x2="16" y2="21"
              stroke="${color}" stroke-width="4.5" stroke-linecap="round"/>
        <polygon points="5,17 27,17 16,32" fill="${color}"/>
       </g>`
    : `<circle cx="16" cy="16" r="5" fill="${color}"
               stroke="white" stroke-width="2"/>`;

  // Weather condition indicators — appear below the arrow area
  const hasPrecip = m.precipitation != null && m.precipitation > 0;
  const hasCloud  = m.humidity      != null && m.humidity >= 90;

  // extraH extends the 32-unit viewBox downward; pixel height scales with size
  let extraH     = 0;
  let weatherSvg = '';
  if (hasCloud && hasPrecip) {
    extraH     = 24;
    weatherSvg = _cloudSvg(33) + _dropsSvg(50);
  } else if (hasCloud) {
    extraH     = 14;
    weatherSvg = _cloudSvg(33);
  } else if (hasPrecip) {
    extraH     = 14;
    weatherSvg = _dropsSvg(38);
  }

  const vbH = 32 + extraH;
  const pxH = size + Math.round(size * extraH / 32);

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${pxH}" viewBox="0 0 32 ${vbH}">
    ${arrow}
    <circle cx="16" cy="16" r="3.5" fill="${centerFill}" stroke="rgba(0,0,0,0.5)" stroke-width="1"/>
    ${weatherSvg}
  </svg>`;

  return L.divIcon({
    html: svg,
    className: '',
    iconSize:    [size, pxH],
    iconAnchor:  [size / 2, size / 2],
    popupAnchor: [0, -(size / 2 + 4)],
  });
}

// ---------------------------------------------------------------------------
// Formatting helpers (shared with popup)
// ---------------------------------------------------------------------------
function _fmt(val, dec) {
  return (val != null) ? val.toFixed(dec) : '<span class="popup-missing">—</span>';
}

function _windArrow(deg) {
  if (deg == null) return '<span class="popup-missing">—</span>';
  const names = ['N','NNE','NE','ENE','E','ESE','SE','SSE','S','SSW','SW','WSW','W','WNW','NW','NNW'];
  const name = names[Math.round(((deg % 360) + 360) % 360 / 22.5) % 16];
  return `<span style="display:inline-block;transform:rotate(${deg}deg)">↓</span> ${name} (${Math.round(deg)}°)`;
}

function _ageLabel(ts) {
  if (!ts) return null;
  const mins = Math.round((Date.now() - new Date(ts).getTime()) / 60000);
  if (mins < -1)  return '📡 Forecast';
  if (mins < 2)   return 'just now';
  if (mins < 60)  return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)   return `${hrs} h ago`;
  return `${Math.floor(hrs / 24)} d ago`;
}

function _ageCssClass(ts) {
  if (!ts) return 'unknown';
  const mins = (Date.now() - new Date(ts).getTime()) / 60000;
  if (mins < -1)  return 'forecast';
  if (mins < 30)  return 'fresh';
  if (mins < 120) return 'recent';
  return 'stale';
}

// ---------------------------------------------------------------------------
// Build popup HTML
// ---------------------------------------------------------------------------
function buildPopup(s) {
  const m = s.latest || {};
  const netColor = NETWORK_COLOR[s.network] || '#a0aec0';
  const badge = `<span class="network-badge network-${s.network || 'unknown'}">${s.network || '?'}</span>`;
  const age = _ageLabel(m.timestamp);
  const ageCls = _ageCssClass(m.timestamp);

  const rows = [
    [window.t('map.popup.wind'),        `${_fmt(m.wind_speed, 1)} km/h  ${_windArrow(m.wind_direction)}`],
    [window.t('map.popup.gust'),        `${_fmt(m.wind_gust, 1)} km/h`],
    [window.t('map.popup.temperature'), `${_fmt(m.temperature, 1)} °C`],
    [window.t('map.popup.humidity'),    `${_fmt(m.humidity, 0)} %`],
    [window.t('map.popup.qff'),         `${_fmt(m.pressure_qff, 1)} hPa`],
  ].map(([label, val]) =>
    `<div class="popup-row"><span>${label}</span><span>${val}</span></div>`
  ).join('');

  const elev = s.elevation != null ? `${s.elevation} m · ` : '';
  const canton = s.canton ? `${s.canton} · ` : '';

  return `
    <div class="popup-name">${s.name}</div>
    <div class="popup-meta">${badge} ${canton}${elev}<span class="freshness ${ageCls}">${age || '—'}</span></div>
    ${rows}
    <a class="popup-link" href="/station-detail?station_id=${encodeURIComponent(s.station_id)}">${window.t('map.popup.view_history')}</a>
  `;
}

// ---------------------------------------------------------------------------
// Föhn station marker icon — badge shape coloured by foehn_active value
// ---------------------------------------------------------------------------
const FOEHN_ABBR = {
  haslital:  'HS',
  beo:       'BEO',
  wallis:    'WL',
  reussthal: 'RS',
  rheintal:  'RT',
  guggi:     'GG',
  overall:   '∑',
};

function foehnStatusColor(foehnActive) {
  if (foehnActive == null || foehnActive < -0.5) return '#a0aec0'; // grey = no data
  if (foehnActive >= 0.9) return '#fc8181';  // red = active
  if (foehnActive >= 0.4) return '#f6ad55';  // orange = partial
  return '#68d391';                           // green = inactive
}

function foehnMarkerIcon(station) {
  const m = station.latest || {};
  const active = m.foehn_active ?? null;
  const color  = foehnStatusColor(active);

  // Extract region key from station_id like "foehn-haslital" → "haslital"
  const key  = (station.station_id || '').replace(/^foehn-/, '');
  const abbr = FOEHN_ABBR[key] || '?';

  // Compact hexagonal/badge SVG: 40×44 px viewBox, flat-topped hexagon with text
  const isOverall = key === 'overall';
  const size = isOverall ? 32 : 26;
  const fontSize = abbr.length > 2 ? 9 : 11;

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 40 40">
    <rect x="2" y="2" width="36" height="36" rx="8" ry="8"
          fill="${color}" stroke="rgba(0,0,0,0.55)" stroke-width="2"/>
    <rect x="4" y="4" width="32" height="32" rx="6" ry="6"
          fill="none" stroke="rgba(255,255,255,0.35)" stroke-width="1"/>
    <text x="20" y="24" text-anchor="middle" dominant-baseline="middle"
          font-family="sans-serif" font-size="${fontSize}" font-weight="700"
          fill="white" stroke="rgba(0,0,0,0.4)" stroke-width="0.6">${abbr}</text>
  </svg>`;

  return L.divIcon({
    html: svg,
    className: '',
    iconSize:    [size, size],
    iconAnchor:  [size / 2, size / 2],
    popupAnchor: [0, -(size / 2 + 4)],
  });
}

function buildFoehnPopup(station) {
  const m      = station.latest || {};
  const active = m.foehn_active ?? null;
  const color  = foehnStatusColor(active);
  const badge  = `<span class="network-badge network-foehn">foehn</span>`;

  let statusLabel;
  if (active == null || active < -0.5) statusLabel = window.t('map.popup.foehn_no_data');
  else if (active >= 0.9)              statusLabel = window.t('map.popup.foehn_active');
  else if (active >= 0.4)              statusLabel = window.t('map.popup.foehn_partial');
  else                                 statusLabel = window.t('map.popup.foehn_inactive');

  const age    = _ageLabel(m.timestamp);
  const ageCls = _ageCssClass(m.timestamp);

  return `
    <div class="popup-name">${station.name}</div>
    <div class="popup-meta">${badge} <span class="freshness ${ageCls}">${age || '—'}</span></div>
    <div class="popup-row">
      <span>${window.t('map.popup.status')}</span>
      <span style="color:${color};font-weight:600">${statusLabel}</span>
    </div>
    <a class="popup-link" href="/foehn">${window.t('map.popup.foehn_dashboard')}</a>
  `;
}

// ---------------------------------------------------------------------------
// Marker layers — professional stations always visible; personal toggleable
// ---------------------------------------------------------------------------
const markerLayer    = L.layerGroup().addTo(map);  // professional + foehn
const _personalLayer = L.layerGroup().addTo(map);  // wunderground, ecowitt, …
let   _showPersonal  = true;

// Leaflet control: "personal stations" toggle button
const _PersonalToggle = L.Control.extend({
  onAdd() {
    const btn = L.DomUtil.create('button');
    Object.assign(btn.style, {
      background: '#1a1f2e',
      border: '1px solid #2d3748',
      borderRadius: '6px',
      color: '#f6ad55',
      cursor: 'pointer',
      fontSize: '0.78rem',
      fontFamily: 'inherit',
      fontWeight: '500',
      padding: '5px 10px',
      lineHeight: '1.4',
      boxShadow: '0 1px 4px rgba(0,0,0,0.4)',
      whiteSpace: 'nowrap',
    });
    btn.title = 'Toggle personal weather stations (Wunderground, Ecowitt, …)';
    function update() {
      const t = typeof window.t === 'function' ? window.t : k => k;
      btn.textContent = _showPersonal ? t('map.toggle_personal_on') : t('map.toggle_personal_off');
      btn.style.opacity = _showPersonal ? '1' : '0.55';
    }
    update();
    // i18n loads after this control is created (module script is deferred).
    // Re-render once translations are ready so the button shows real text.
    document.addEventListener('i18nReady', update, { once: true });
    L.DomEvent.on(btn, 'click', L.DomEvent.stopPropagation);
    L.DomEvent.on(btn, 'click', () => {
      _showPersonal = !_showPersonal;
      if (_showPersonal) {
        _personalLayer.addTo(map);
      } else {
        map.removeLayer(_personalLayer);
      }
      update();
    });
    return btn;
  },
});
new _PersonalToggle({ position: 'topright' }).addTo(map);

// ---------------------------------------------------------------------------
// Geolocation centering (specs/008-progressive-map-loading)
// First visit: attempt geolocation once, non-blocking — the map above is
// already painted at the Interlaken default regardless of the outcome. A prior
// explicit decline is remembered and not re-prompted; a prior grant re-resolves
// fresh on every visit. The explicit "center on me" control below can always
// retry, regardless of the stored preference.
// ---------------------------------------------------------------------------
const _GEO_PREF_KEY = 'lenti_geo_pref';

function _tryGeolocate(onSettle) {
  if (!('geolocation' in navigator)) { onSettle(null); return; }
  navigator.geolocation.getCurrentPosition(
    pos => {
      localStorage.setItem(_GEO_PREF_KEY, 'granted');
      onSettle({ lat: pos.coords.latitude, lon: pos.coords.longitude });
    },
    err => {
      // Only an explicit denial is remembered — a timeout or unavailable position
      // must not permanently overwrite a prior grant (it retries next visit).
      if (err.code === err.PERMISSION_DENIED) localStorage.setItem(_GEO_PREF_KEY, 'declined');
      console.warn('[Lenti:map] geolocation unavailable:', err.message);
      onSettle(null);
    },
    { timeout: 8000 },
  );
}

if (localStorage.getItem(_GEO_PREF_KEY) !== 'declined') {
  _tryGeolocate(pos => {
    if (pos) {
      console.log(`[Lenti:map] geolocation resolved — recentering to ${pos.lat.toFixed(4)}, ${pos.lon.toFixed(4)}`);
      map.setView([pos.lat, pos.lon], 11);
    }
  });
}

const _GeolocateControl = L.Control.extend({
  onAdd() {
    const btn = L.DomUtil.create('button');
    Object.assign(btn.style, {
      background: '#1a1f2e',
      border: '1px solid #2d3748',
      borderRadius: '6px',
      color: '#63b3ed',
      cursor: 'pointer',
      fontSize: '0.78rem',
      fontFamily: 'inherit',
      fontWeight: '500',
      padding: '5px 10px',
      lineHeight: '1.4',
      boxShadow: '0 1px 4px rgba(0,0,0,0.4)',
      whiteSpace: 'nowrap',
      marginTop: '6px',
    });
    btn.title = 'Center the map on my current location';
    function update() {
      const t = typeof window.t === 'function' ? window.t : k => k;
      btn.textContent = t('map.geolocate_button');
    }
    update();
    // i18n loads after this control is created (module script is deferred).
    document.addEventListener('i18nReady', update, { once: true });
    L.DomEvent.on(btn, 'click', L.DomEvent.stopPropagation);
    L.DomEvent.on(btn, 'click', () => {
      console.log('[Lenti:map] geolocate control clicked');
      _tryGeolocate(pos => { if (pos) map.setView([pos.lat, pos.lon], 11); });
    });
    return btn;
  },
});
new _GeolocateControl({ position: 'topright' }).addTo(map);

// ---------------------------------------------------------------------------
// Viewport-first progressive rendering (specs/008-progressive-map-loading)
// Stations inside the current map bounds are placed immediately; the rest are
// placed in small chunks during idle time so a large station count never
// blocks the initial paint. Panning/zooming re-prioritizes the still-pending
// queue by promoting newly-visible stations, without touching or duplicating
// markers already placed.
// ---------------------------------------------------------------------------
let _renderGen = 0;
let _pendingOffscreen = [];

function _placeStationMarker(s) {
  if (s.latitude == null || s.longitude == null) return false;
  const isFoehn    = s.network === 'foehn';
  const isPersonal = PERSONAL_NETWORKS.has(s.network);
  const icon  = isFoehn ? foehnMarkerIcon(s) : markerIcon(s);
  const layer = isPersonal ? _personalLayer : markerLayer;
  // Lazy popup: built only when opened, so window.t is guaranteed to be ready
  L.marker([s.latitude, s.longitude], { icon })
    .addTo(layer)
    .bindPopup(() => isFoehn ? buildFoehnPopup(s) : buildPopup(s), { maxWidth: 260 });
  return true;
}

function _partitionByViewport(stations) {
  const bounds = map.getBounds();
  const visible = [], offscreen = [];
  for (const s of stations) {
    if (s.latitude == null || s.longitude == null) continue;
    (bounds.contains([s.latitude, s.longitude]) ? visible : offscreen).push(s);
  }
  return { visible, offscreen };
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

// Places the visible tier synchronously, defers the rest. The caller must clear
// layers first when rendering fresh data (a full new fetch/frame) — moveend
// re-prioritization below intentionally does NOT go through this function, since
// it only promotes still-pending stations and must never re-place a marker
// that's already on the map.
function _renderStationsViewportFirst(stations, placeFn) {
  const gen = ++_renderGen;
  const { visible, offscreen } = _partitionByViewport(stations);
  visible.forEach(placeFn);
  _pendingOffscreen = offscreen;
  _deferChunked(placeFn, gen);
  return { visibleCount: visible.length, offscreenCount: offscreen.length };
}

// Pan/zoom re-prioritization (FR-005): promote any now-visible station still
// waiting in the deferred queue. No-op once the deferred queue has drained.
map.on('moveend', () => {
  if (_pendingOffscreen.length === 0) return;
  const bounds = map.getBounds();
  const stillPending = [];
  for (const s of _pendingOffscreen) {
    if (bounds.contains([s.latitude, s.longitude])) _placeStationMarker(s);
    else stillPending.push(s);
  }
  _pendingOffscreen = stillPending;
});

// ---------------------------------------------------------------------------
// Load stations and place markers
// ---------------------------------------------------------------------------

// Exposed globally so ruleset popup builders can resolve station names
window._lentiStationsMap = {};

async function loadStations() {
  const t0 = performance.now();
  console.log('[Lenti:map] loadStations — fetching /api/stations');
  try {
    // Cache-buster ensures the browser never serves a stale response
    const res = await fetch(`/api/stations?_t=${Date.now()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const stations = await res.json();
    console.log(`[Lenti:map] /api/stations → ${stations.length} stations in ${(performance.now()-t0).toFixed(0)}ms`);

    // Update global station lookup
    window._lentiStationsMap = {};
    for (const s of stations) window._lentiStationsMap[s.station_id] = s;

    markerLayer.clearLayers();
    _personalLayer.clearLayers();

    const { visibleCount, offscreenCount } = _renderStationsViewportFirst(stations, _placeStationMarker);
    const placed = visibleCount + offscreenCount;

    console.log(`[Lenti:map] ${placed} markers placed (${visibleCount} visible now, ${offscreenCount} deferred), total loadStations time: ${(performance.now()-t0).toFixed(0)}ms`);
    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
    setStatus(true, `${placed} station${placed !== 1 ? 's' : ''} · ${now}`);
  } catch (err) {
    console.error('[Lenti:map] Failed to load stations:', err);
    setStatus(false, 'Error');
  }
}

function setStatus(ok, label) {
  document.getElementById('dbDot').className = `dot ${ok ? 'green' : 'grey'}`;
  document.getElementById('navStatus').textContent = label;
}

// ---------------------------------------------------------------------------
// Live auto-refresh (disabled during replay)
// ---------------------------------------------------------------------------
let _liveTimer = null;

function startLiveRefresh() {
  stopLiveRefresh();
  _liveTimer = setInterval(loadStations, 60_000);
}

function stopLiveRefresh() {
  if (_liveTimer) { clearInterval(_liveTimer); _liveTimer = null; }
}

window._stationsReady = loadStations();
startLiveRefresh();

// ---------------------------------------------------------------------------
// Replay integration — called by the replay bar (inline script in index.html)
// ---------------------------------------------------------------------------
function applyReplaySnapshot(stations) {
  markerLayer.clearLayers();
  _personalLayer.clearLayers();
  const { visibleCount, offscreenCount } = _renderStationsViewportFirst(stations, _placeStationMarker);
  const placed = visibleCount + offscreenCount;
  if (placed === 0 && stations.length > 0)
    console.warn(`[Lenti:map] applyReplaySnapshot: ${stations.length} stations in snapshot but 0 placed (all missing lat/lon?)`);
  return placed;
}
