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
// Marker icon — directional arrow (tip points where wind travels TO),
// rotated by wind_direction, coloured + sized by wind_gust / wind_speed.
// Dark outline ensures visibility against the light OpenTopoMap background.
// ---------------------------------------------------------------------------
function markerIcon(station) {
  const m      = station.latest || {};
  const gust   = m.wind_gust ?? m.wind_speed;
  const color  = windSpeedColor(gust);
  const dir    = m.wind_direction ?? 0;
  const hasDir = m.wind_direction != null;
  const size   = markerSize(gust);

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

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="${size}" height="${size}" viewBox="0 0 32 32">
    ${arrow}
    <circle cx="16" cy="16" r="3.5" fill="white" stroke="rgba(0,0,0,0.5)" stroke-width="1"/>
  </svg>`;

  return L.divIcon({
    html: svg,
    className: '',
    iconSize:    [size, size],
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
// Load stations and place markers
// ---------------------------------------------------------------------------
const markerLayer = L.layerGroup().addTo(map);

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

    let placed = 0;
    for (const s of stations) {
      if (s.latitude == null || s.longitude == null) continue;
      L.marker([s.latitude, s.longitude], { icon: markerIcon(s) })
        .addTo(markerLayer)
        .bindPopup(buildPopup(s), { maxWidth: 260 });
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
  let placed = 0;
  for (const s of stations) {
    if (s.latitude == null || s.longitude == null) continue;
    L.marker([s.latitude, s.longitude], { icon: markerIcon(s) })
      .addTo(markerLayer)
      .bindPopup(buildPopup(s), { maxWidth: 260 });
    placed++;
  }
  return placed;
}
