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
    ['Wind',  `${_fmt(m.wind_speed, 1)} km/h  ${_windArrow(m.wind_direction)}`],
    ['Gust',  `${_fmt(m.wind_gust, 1)} km/h`],
    ['Temp',  `${_fmt(m.temperature, 1)} °C`],
    ['Hum',   `${_fmt(m.humidity, 0)} %`],
    ['QNH',   `${_fmt(m.pressure_qnh, 1)} hPa`],
  ].map(([label, val]) =>
    `<div class="popup-row"><span>${label}</span><span>${val}</span></div>`
  ).join('');

  const elev = s.elevation != null ? `${s.elevation} m · ` : '';
  const canton = s.canton ? `${s.canton} · ` : '';

  return `
    <div class="popup-name">${s.name}</div>
    <div class="popup-meta">${badge} ${canton}${elev}<span class="freshness ${ageCls}">${age || '—'}</span></div>
    ${rows}
    <a class="popup-link" href="/station-detail?station_id=${encodeURIComponent(s.station_id)}">View history →</a>
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
  if (active == null || active < -0.5) statusLabel = 'No data';
  else if (active >= 0.9)              statusLabel = 'Active';
  else if (active >= 0.4)              statusLabel = 'Partial';
  else                                 statusLabel = 'Inactive';

  const age    = _ageLabel(m.timestamp);
  const ageCls = _ageCssClass(m.timestamp);

  return `
    <div class="popup-name">${station.name}</div>
    <div class="popup-meta">${badge} <span class="freshness ${ageCls}">${age || '—'}</span></div>
    <div class="popup-row">
      <span>Status</span>
      <span style="color:${color};font-weight:600">${statusLabel}</span>
    </div>
    <a class="popup-link" href="/foehn">Föhn dashboard →</a>
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
      btn.textContent = _showPersonal ? '⚠ Personal stations: ON' : '⚠ Personal stations: OFF';
      btn.style.opacity = _showPersonal ? '1' : '0.55';
    }
    update();
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
// Load stations and place markers
// ---------------------------------------------------------------------------

// Exposed globally so ruleset popup builders can resolve station names
window._lentiStationsMap = {};

async function loadStations() {
  try {
    // Cache-buster ensures the browser never serves a stale response
    const res = await fetch(`/api/stations?_t=${Date.now()}`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const stations = await res.json();

    // Update global station lookup
    window._lentiStationsMap = {};
    for (const s of stations) window._lentiStationsMap[s.station_id] = s;

    markerLayer.clearLayers();
    _personalLayer.clearLayers();

    let placed = 0;
    for (const s of stations) {
      if (s.latitude == null || s.longitude == null) continue;
      const isFoehn   = s.network === 'foehn';
      const isPersonal = PERSONAL_NETWORKS.has(s.network);
      const icon  = isFoehn ? foehnMarkerIcon(s) : markerIcon(s);
      const popup = isFoehn ? buildFoehnPopup(s) : buildPopup(s);
      const layer = isPersonal ? _personalLayer : markerLayer;
      L.marker([s.latitude, s.longitude], { icon })
        .addTo(layer)
        .bindPopup(popup, { maxWidth: 260 });
      placed++;
    }

    const now = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    setStatus(true, `${placed} station${placed !== 1 ? 's' : ''} · ${now}`);
  } catch (err) {
    console.error('Failed to load stations:', err);
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

loadStations();
startLiveRefresh();

// ---------------------------------------------------------------------------
// Replay integration — called by the replay bar (inline script in index.html)
// ---------------------------------------------------------------------------
function applyReplaySnapshot(stations) {
  markerLayer.clearLayers();
  _personalLayer.clearLayers();
  let placed = 0;
  for (const s of stations) {
    if (s.latitude == null || s.longitude == null) continue;
    const isFoehn    = s.network === 'foehn';
    const isPersonal = PERSONAL_NETWORKS.has(s.network);
    const icon  = isFoehn ? foehnMarkerIcon(s) : markerIcon(s);
    const popup = isFoehn ? buildFoehnPopup(s) : buildPopup(s);
    const layer = isPersonal ? _personalLayer : markerLayer;
    L.marker([s.latitude, s.longitude], { icon })
      .addTo(layer)
      .bindPopup(popup, { maxWidth: 260 });
    placed++;
  }
  return placed;
}
