/**
 * Lenticularis — main dashboard script (MVP 0.1)
 *
 * Fetches /api/stations and renders a sortable, filterable data table.
 */

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let _allStations = [];
let _sortKey = 'name';
let _sortAsc = true;
let _autoRefreshTimer = null;

const AUTO_REFRESH_SECONDS = 120;

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  loadStations();
  startAutoRefresh();

  document.getElementById('searchInput').addEventListener('input', renderTable);
  document.getElementById('networkFilter').addEventListener('change', renderTable);
});

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------
async function loadStations() {
  const btn = document.getElementById('refreshBtn');
  btn.disabled = true;
  btn.textContent = '↻ Loading…';

  try {
    const res = await fetch('/api/stations');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _allStations = await res.json();

    setStatus(true);
    document.getElementById('lastRefresh').textContent =
      'Updated ' + new Date().toLocaleTimeString();
  } catch (err) {
    console.error('Failed to load stations:', err);
    setStatus(false);
  } finally {
    btn.disabled = false;
    btn.textContent = '↻ Refresh';
    renderTable();
  }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------
function renderTable() {
  const search = document.getElementById('searchInput').value.toLowerCase();
  const networkFilter = document.getElementById('networkFilter').value;

  let filtered = _allStations.filter(s => {
    const matchSearch =
      !search ||
      s.name.toLowerCase().includes(search) ||
      (s.canton || '').toLowerCase().includes(search) ||
      s.station_id.toLowerCase().includes(search);
    const matchNetwork = !networkFilter || s.network === networkFilter;
    return matchSearch && matchNetwork;
  });

  // Sorting
  filtered.sort((a, b) => {
    let av = _getSortValue(a, _sortKey);
    let bv = _getSortValue(b, _sortKey);
    if (av === null || av === undefined) av = _sortAsc ? Infinity : -Infinity;
    if (bv === null || bv === undefined) bv = _sortAsc ? Infinity : -Infinity;
    if (typeof av === 'string') return _sortAsc ? av.localeCompare(bv) : bv.localeCompare(av);
    return _sortAsc ? av - bv : bv - av;
  });

  document.getElementById('countBadge').textContent = `${filtered.length} station${filtered.length !== 1 ? 's' : ''}`;

  const loadingEl = document.getElementById('loadingState');
  const emptyEl = document.getElementById('emptyState');
  const tableEl = document.getElementById('stationTable');
  const tbody = document.getElementById('tableBody');

  loadingEl.style.display = 'none';

  if (filtered.length === 0) {
    emptyEl.style.display = 'block';
    tableEl.style.display = 'none';
    return;
  }

  emptyEl.style.display = 'none';
  tableEl.style.display = 'table';

  tbody.innerHTML = filtered.map(station => renderRow(station)).join('');
}

function renderRow(s) {
  const m = s.latest || {};

  const wind    = _fmt(m.wind_speed, 1);
  const gust    = _fmt(m.wind_gust, 1);
  const temp    = _fmt(m.temperature, 1);
  const hum     = _fmt(m.humidity, 0);
  const qnh     = _fmt(m.pressure_qnh, 1);
  const precip  = _fmt(m.precipitation, 1);
  const dirArrow = _windArrow(m.wind_direction);
  const age     = s.latest?.timestamp ? _ageLabel(s.latest.timestamp) : null;
  const ageCls  = _ageCssClass(s.latest?.timestamp);
  const netBadge = `<span class="network-badge network-${s.network || 'unknown'}">${s.network || '?'}</span>`;

  return `<tr>
    <td title="${s.station_id}"><a class="station-link" href="station-detail.html?station_id=${encodeURIComponent(s.station_id)}">${_esc(s.name)}</a></td>
    <td>${netBadge}</td>
    <td>${s.canton || '<span class="value-missing">—</span>'}</td>
    <td>${s.elevation != null ? s.elevation : '<span class="value-missing">—</span>'}</td>
    <td>${wind}</td>
    <td title="${m.wind_direction != null ? m.wind_direction + '°' : ''}">${dirArrow}</td>
    <td>${gust}</td>
    <td>${temp}</td>
    <td>${hum}</td>
    <td>${qnh}</td>
    <td>${precip}</td>
    <td><span class="freshness ${ageCls}">${age || '<span class="value-missing">—</span>'}</span></td>
  </tr>`;
}

// ---------------------------------------------------------------------------
// Sorting
// ---------------------------------------------------------------------------
function sortBy(key) {
  if (_sortKey === key) {
    _sortAsc = !_sortAsc;
  } else {
    _sortKey = key;
    _sortAsc = true;
  }
  renderTable();
}

function _getSortValue(station, key) {
  switch (key) {
    case 'name':         return station.name;
    case 'network':      return station.network;
    case 'canton':       return station.canton || '';
    case 'elevation':    return station.elevation;
    case '_age':         return station.latest?.timestamp
                           ? new Date(station.latest.timestamp).getTime()
                           : null;
    default:             return station.latest?.[key] ?? null;
  }
}

// ---------------------------------------------------------------------------
// Auto-refresh
// ---------------------------------------------------------------------------
function startAutoRefresh() {
  if (_autoRefreshTimer) clearInterval(_autoRefreshTimer);
  _autoRefreshTimer = setInterval(loadStations, AUTO_REFRESH_SECONDS * 1000);
}

// ---------------------------------------------------------------------------
// Status indicator
// ---------------------------------------------------------------------------
function setStatus(ok) {
  const dot = document.getElementById('dbDot');
  const label = document.getElementById('dbLabel');
  dot.className = 'dot ' + (ok ? 'green' : 'grey');
  label.textContent = ok ? 'Live' : 'Error';
}

// ---------------------------------------------------------------------------
// Formatting helpers
// ---------------------------------------------------------------------------
function _fmt(value, decimals) {
  if (value === null || value === undefined) return '<span class="value-missing">—</span>';
  return (+value).toFixed(decimals);
}

function _windArrow(degrees) {
  if (degrees === null || degrees === undefined) return '<span class="value-missing">—</span>';
  // 0° = N, arrow points in the direction the wind is blowing FROM
  return `<span class="wind-dir-arrow" style="display:inline-block;transform:rotate(${degrees}deg)" title="${degrees}°">↑</span>`;
}

function _ageLabel(isoString) {
  if (!isoString) return null;
  const diff = Math.floor((Date.now() - new Date(isoString).getTime()) / 1000);
  if (diff < 60)   return `${diff}s ago`;
  if (diff < 3600) return `${Math.floor(diff / 60)}m ago`;
  if (diff < 86400) return `${Math.floor(diff / 3600)}h ago`;
  return `${Math.floor(diff / 86400)}d ago`;
}

function _ageCssClass(isoString) {
  if (!isoString) return 'unknown';
  const diff = (Date.now() - new Date(isoString).getTime()) / 1000;
  if (diff < 900)  return 'fresh';   // < 15 min
  if (diff < 3600) return 'recent';  // < 1 hour
  return 'stale';
}

function _esc(str) {
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}
