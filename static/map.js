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
  zoom: 10,
  zoomControl: true,
});

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
// 0–25 km/h : white → dark green
// 25–35 km/h : dark green → dark blue
// 35–50 km/h : dark blue → bright red
// 50+ km/h  : bright red
// ---------------------------------------------------------------------------
function windSpeedColor(speed) {
  if (speed == null) return '#718096'; // grey = no data

  function lerp(a, b, t) { return Math.round(a + (b - a) * t); }
  function lerpRGB(c1, c2, t) {
    return `rgb(${lerp(c1[0],c2[0],t)},${lerp(c1[1],c2[1],t)},${lerp(c1[2],c2[2],t)})`;
  }

  const white     = [255, 255, 255];
  const darkGreen = [ 39, 103,  73];
  const darkBlue  = [ 43,  77, 160];
  const brightRed = [229,  62,  62];

  if (speed <=  0) return 'rgb(255,255,255)';
  if (speed <= 25) return lerpRGB(white,     darkGreen, speed / 25);
  if (speed <= 35) return lerpRGB(darkGreen, darkBlue,  (speed - 25) / 10);
  if (speed <= 50) return lerpRGB(darkBlue,  brightRed, (speed - 35) / 15);
  return 'rgb(229,62,62)';
}

// ---------------------------------------------------------------------------
// Marker icon — directional arrow (down = direction wind travels to),
// rotated by wind_direction, coloured by wind_speed
// ---------------------------------------------------------------------------
function markerIcon(station) {
  const m       = station.latest || {};
  const color   = windSpeedColor(m.wind_gust ?? m.wind_speed);
  const dir     = m.wind_direction ?? 0;
  const hasDir  = m.wind_direction != null;

  const arrow = hasDir
    ? `<g transform="rotate(${dir},16,16)">
        <line x1="16" y1="4" x2="16" y2="20"
              stroke="${color}" stroke-width="2.5" stroke-linecap="round"/>
        <polygon points="9,20 23,20 16,28" fill="${color}"/>
       </g>`
    : `<circle cx="16" cy="16" r="4" fill="${color}"/>`;

  const svg = `<svg xmlns="http://www.w3.org/2000/svg" width="32" height="32" viewBox="0 0 32 32">
    <circle cx="16" cy="16" r="14" fill="#0f1117" fill-opacity="0.80"
            stroke="${color}" stroke-width="1.5"/>
    ${arrow}
  </svg>`;

  return L.divIcon({
    html: svg,
    className: '',
    iconSize: [32, 32],
    iconAnchor: [16, 16],
    popupAnchor: [0, -18],
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
  if (mins < 2)   return 'just now';
  if (mins < 60)  return `${mins} min ago`;
  const hrs = Math.floor(mins / 60);
  if (hrs < 24)   return `${hrs} h ago`;
  return `${Math.floor(hrs / 24)} d ago`;
}

function _ageCssClass(ts) {
  if (!ts) return 'unknown';
  const mins = (Date.now() - new Date(ts).getTime()) / 60000;
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
async function loadStations() {
  try {
    const res = await fetch('/api/stations');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const stations = await res.json();

    let placed = 0;
    for (const s of stations) {
      if (s.latitude == null || s.longitude == null) continue;
      L.marker([s.latitude, s.longitude], { icon: markerIcon(s) })
        .addTo(map)
        .bindPopup(buildPopup(s), { maxWidth: 260 });
      placed++;
    }

    setStatus(true, `${placed} station${placed !== 1 ? 's' : ''}`);
  } catch (err) {
    console.error('Failed to load stations:', err);
    setStatus(false, 'Error');
  }
}

function setStatus(ok, label) {
  document.getElementById('dbDot').className = `dot ${ok ? 'green' : 'grey'}`;
  document.getElementById('navStatus').textContent = label;
}

loadStations();
