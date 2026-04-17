/**
 * Lenticularis — forecast-accuracy.js
 *
 * Lets the user pick a station, a start date and a time range (24h–30d),
 * then renders per-field Chart.js charts showing observed values overlaid
 * with up to 3 separate model-run forecast lines (one per calendar init_date).
 */

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const CHART_COLORS = {
  wind_speed:     '#63b3ed',
  wind_gust:      '#fc8181',
  wind_direction: '#76e4f7',
  temperature:    '#f6ad55',
  humidity:       '#76e4f7',
  pressure:       '#b794f4',
  precipitation:  '#4299e1',
  snow_depth:     '#bee3f8',
};

// Forecast line colors per index (index 0 = most recent init_date)
const FORECAST_COLORS = [
  '#68d391', // green  – most recent (yesterday)
  '#9f7aea', // purple – D-2
  '#f687b3', // pink   – D-3
  '#f6ad55', // amber  – older / legacy
];

function fcColor(idx) {
  return FORECAST_COLORS[Math.min(idx, FORECAST_COLORS.length - 1)];
}

// ---------------------------------------------------------------------------
// Cursor guide plugin
// ---------------------------------------------------------------------------
const cursorGuidePlugin = {
  id: 'cursorGuide',
  afterEvent(chart, args) {
    if (args.event.type === 'mousemove') {
      chart._cursorX = args.event.x;
      args.changed = true;
    } else if (args.event.type === 'mouseout') {
      chart._cursorX = null;
      args.changed = true;
    }
  },
  afterDraw(chart) {
    const x = chart._cursorX;
    if (x == null) return;
    const { chartArea, ctx } = chart;
    if (x < chartArea.left || x > chartArea.right) return;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, chartArea.top);
    ctx.lineTo(x, chartArea.bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(226, 232, 240, 0.45)';
    ctx.setLineDash([4, 4]);
    ctx.stroke();
    ctx.restore();
  },
};

Chart.register(cursorGuidePlugin);

if (Chart.Tooltip?.positioners) {
  Chart.Tooltip.positioners.topCursor = function topCursor(items, eventPosition) {
    if (!items || items.length === 0) return false;
    return { x: eventPosition.x, y: this.chart.chartArea.top + 8 };
  };
}

const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  plugins: {
    legend: { display: true, labels: { color: '#a0aec0', boxWidth: 12, font: { size: 11 } } },
    tooltip: {
      mode: 'x',
      intersect: false,
      position: 'topCursor',
      backgroundColor: '#1a1f2e',
      borderColor: '#2d3748',
      borderWidth: 1,
      titleColor: '#a0aec0',
      bodyColor: '#e2e8f0',
      callbacks: {
        title: (items) => {
          if (!items.length) return '';
          // Use the item with the closest x to the cursor (they're already sorted by mode:'x')
          const ts = items[0].parsed.x;
          return new Date(ts).toLocaleString(undefined, {
            month: '2-digit', day: '2-digit',
            hour: '2-digit', minute: '2-digit', hour12: false,
          });
        },
      },
    },
    cursorGuide: { enabled: true },
  },
  scales: {
    x: {
      type: 'time',
      time: { tooltipFormat: 'dd.MM HH:mm' },
      ticks: { color: '#718096', maxRotation: 0, autoSkipPadding: 20 },
      grid: { color: '#1e2533' },
    },
    y: {
      ticks: { color: '#718096' },
      grid: { color: '#1e2533' },
    },
  },
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let _stationId = null;
let _fromDate  = null; // YYYY-MM-DD (UTC midnight as start)
let _hours     = 24;
const _charts  = {};

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', async () => {
  const params = new URLSearchParams(window.location.search);
  const presetStation = params.get('station_id');

  // Default: 2 days ago — ensures both actual observations and forecasts are available
  const twoDaysAgo = new Date(Date.now() - 2 * 86400000);
  _fromDate = twoDaysAgo.toISOString().slice(0, 10);
  document.getElementById('fromDate').value = _fromDate;

  await loadStationList(presetStation);
});

// ---------------------------------------------------------------------------
// Station list
// ---------------------------------------------------------------------------
async function loadStationList(presetId) {
  try {
    const res = await fetch('/api/stations');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const stations = await res.json();

    const sel = document.getElementById('stationPicker');
    stations.forEach(s => {
      const opt = document.createElement('option');
      opt.value = s.station_id;
      opt.textContent = `${s.name}${s.canton ? ' · ' + s.canton : ''} (${s.network})`;
      if (s.station_id === presetId) opt.selected = true;
      sel.appendChild(opt);
    });

    if (presetId) {
      _stationId = presetId;
      const st = stations.find(s => s.station_id === presetId);
      if (st) renderStationMeta(st);
      loadData();
    }
  } catch (err) {
    showError(`Could not load station list: ${err.message}`);
  }
}

function onStationChange() {
  const sel = document.getElementById('stationPicker');
  _stationId = sel.value || null;
  if (!_stationId) { clearCharts(); return; }

  document.getElementById('stationName').textContent =
    sel.options[sel.selectedIndex].textContent.split(' (')[0];
  document.getElementById('networkBadge').textContent = '';
  document.getElementById('stationCanton').textContent = '';
  document.getElementById('stationElevation').textContent = '';
  loadData();
}

function renderStationMeta(station) {
  document.title = `Lenticularis — ${station.name} Forecast Accuracy`;
  document.getElementById('stationName').textContent = station.name;

  const netBadge = document.getElementById('networkBadge');
  netBadge.textContent = station.network;
  netBadge.className = `network-badge network-${station.network || 'unknown'}`;

  const t = typeof window.t === 'function' ? window.t : (k, v) => k;
  document.getElementById('stationCanton').textContent =
    station.canton ? t('common.canton', { name: station.canton }) : '';
  document.getElementById('stationElevation').textContent =
    station.elevation != null ? t('common.elevation_asl', { m: station.elevation }) : '';
}

// ---------------------------------------------------------------------------
// Date / range controls
// ---------------------------------------------------------------------------
function onDateChange() {
  _fromDate = document.getElementById('fromDate').value;
  if (_stationId) loadData();
}

function setRange(hours) {
  _hours = hours;
  document.querySelectorAll('.range-btn[data-hours]').forEach(btn => {
    btn.classList.toggle('active', +btn.dataset.hours === hours);
  });
  if (_stationId) loadData();
}

// ---------------------------------------------------------------------------
// Data fetch
// ---------------------------------------------------------------------------
async function loadData() {
  if (!_stationId || !_fromDate) return;
  document.getElementById('pickStationNote').style.display = 'none';
  showLoading(true);
  hideError();

  const fromDt = new Date(_fromDate + 'T00:00:00Z');
  const toDt   = new Date(fromDt.getTime() + _hours * 3600000);

  try {
    const url = `/api/stations/${encodeURIComponent(_stationId)}/forecast-accuracy`
      + `?from=${encodeURIComponent(fromDt.toISOString())}`
      + `&to=${encodeURIComponent(toDt.toISOString())}`;
    const res = await fetch(url);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const data = await res.json();
    renderCharts(data, fromDt, toDt);
    setRefreshLabel();
    setDbDot(true);
  } catch (err) {
    showError(`Failed to load data: ${err.message}`);
    setDbDot(false);
  } finally {
    showLoading(false);
  }
}

// ---------------------------------------------------------------------------
// Render all charts
// ---------------------------------------------------------------------------
function renderCharts(data, xMin, xMax) {
  const { actual, forecasts } = data;          // forecasts = [{init_date, data}, ...]
  const t = typeof window.t === 'function' ? window.t : k => k;

  function buildFields(rows) {
    const f = {
      wind_speed: [], wind_gust: [], wind_direction: [],
      temperature: [], humidity: [], pressure: [],
      precipitation: [], snow_depth: [],
    };
    for (const row of (rows || [])) {
      const ts = row.timestamp;
      // pressure_qff is the API field name; normalise to 'pressure' for internal lookups
      if (row.pressure_qff != null) f.pressure.push({ x: ts, y: row.pressure_qff });
      for (const field of ['wind_speed', 'wind_gust', 'wind_direction', 'temperature', 'humidity', 'precipitation', 'snow_depth']) {
        if (row[field] != null) f[field].push({ x: ts, y: row[field] });
      }
    }
    return f;
  }

  const obs = buildFields(actual);
  // Build an array of {label, color, fields} for each forecast series
  const fcSeries = (forecasts || []).map((fc, idx) => ({
    label: fc.init_date
      ? t('forecast_accuracy.legend_fc_date', { date: fc.init_date })
      : t('forecast_accuracy.legend_forecast'),
    color: fcColor(idx),
    fields: buildFields(fc.data),
  }));

  const range = { xMin, xMax };

  const anyData = Object.values(obs).some(a => a.length > 0)
               || fcSeries.some(s => Object.values(s.fields).some(a => a.length > 0));

  document.getElementById('chartsGrid').style.display  = anyData ? 'grid' : 'none';
  document.getElementById('noDataNote').style.display  = anyData ? 'none' : 'block';
  if (!anyData) {
    renderLegend([]);
    return;
  }

  renderLegend(fcSeries, t);

  // Wind speed + gust
  const hasWind = obs.wind_speed.length || obs.wind_gust.length
               || fcSeries.some(s => s.fields.wind_speed.length || s.fields.wind_gust.length);
  showCard('wind', hasWind);
  if (hasWind) renderWindChart(obs, fcSeries, range, t);

  // Wind direction line
  const hasWinDir = obs.wind_direction.length
                 || fcSeries.some(s => s.fields.wind_direction.length);
  showCard('wind_direction_line', hasWinDir);
  if (hasWinDir) renderWindDirLineChart(obs.wind_direction, fcSeries, range, t);

  // Simple fields
  renderSimpleChart('temperature', obs.temperature, fcSeries, range, {}, t);
  renderSimpleChart('humidity',    obs.humidity,    fcSeries, range, { yMin: 0, yMax: 100 }, t);
  renderSimpleChart('pressure',    obs.pressure,     fcSeries, range, {}, t);
  renderPrecipitationChart(obs.precipitation, fcSeries, range, t);
  renderSimpleChart('snow_depth',  obs.snow_depth,  fcSeries, range, { yMin: 0 }, t);
}

// ---------------------------------------------------------------------------
// Dynamic legend strip
// ---------------------------------------------------------------------------
function renderLegend(fcSeries, t) {
  const strip = document.getElementById('legendStrip');
  if (!fcSeries || fcSeries.length === 0) {
    strip.style.display = 'none';
    return;
  }
  strip.style.display = 'flex';
  strip.innerHTML = '';

  // Actual line entry
  const actualEl = document.createElement('div');
  actualEl.className = 'legend-item';
  actualEl.innerHTML = `
    <span class="legend-line" style="background:#63b3ed"></span>
    <span>${t ? t('forecast_accuracy.legend_actual') : 'Actual'}</span>`;
  strip.appendChild(actualEl);

  // One entry per forecast series
  fcSeries.forEach(({ label, color }) => {
    const el = document.createElement('div');
    el.className = 'legend-item';
    el.innerHTML = `
      <span class="legend-line dashed" style="color:${color}"></span>
      <span>${label}</span>`;
    strip.appendChild(el);
  });
}

// ---------------------------------------------------------------------------
// Wind direction line chart
// ---------------------------------------------------------------------------
function renderWindDirLineChart(points, fcSeries, opts, t) {
  const canvasId = 'chart-wind_direction_line';
  destroyChart(canvasId);

  const color = CHART_COLORS.wind_direction;
  const compassLabels = { 0:'N', 45:'NO', 90:'O', 135:'SO', 180:'S', 225:'SW', 270:'W', 315:'NW', 360:'N' };

  const datasets = [
    {
      label: t ? t('forecast_accuracy.legend_actual') : 'Actual',
      data: points,
      borderColor: color,
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0,
      spanGaps: false,
    },
  ];

  fcSeries.forEach(({ label, color: fcClr, fields }) => {
    if (fields.wind_direction.length) {
      datasets.push({
        label,
        data: fields.wind_direction,
        borderColor: fcClr,
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        borderDash: [6, 4],
        pointRadius: 0,
        tension: 0,
        spanGaps: false,
      });
    }
  });

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      plugins: { ...CHART_DEFAULTS.plugins },
      scales: {
        x: { ...CHART_DEFAULTS.scales.x, min: opts.xMin, max: opts.xMax },
        y: {
          min: 0, max: 360,
          ticks: { color: '#718096', stepSize: 45, callback: val => compassLabels[val] ?? val },
          grid: { color: '#1e2533' },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Wind speed + gust
// ---------------------------------------------------------------------------
function renderWindChart(obs, fcSeries, opts, t) {
  const canvasId = 'chart-wind';
  destroyChart(canvasId);

  const labelActual = t ? t('forecast_accuracy.legend_actual') : 'Actual';
  const datasets = [
    {
      label: `${labelActual} — wind`,
      data: obs.wind_speed,
      borderColor: CHART_COLORS.wind_speed,
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.2,
    },
    {
      label: `${labelActual} — gust`,
      data: obs.wind_gust,
      borderColor: CHART_COLORS.wind_gust,
      backgroundColor: hexToRgba(CHART_COLORS.wind_gust, 0.10),
      fill: '-1',
      borderWidth: 1,
      borderDash: [4, 3],
      pointRadius: 0,
      tension: 0.2,
    },
  ];

  fcSeries.forEach(({ label, color, fields }) => {
    if (fields.wind_speed.length) {
      datasets.push({
        label: `${label} — wind`,
        data: fields.wind_speed,
        borderColor: color,
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        borderDash: [6, 4],
        pointRadius: 0,
        tension: 0.2,
      });
    }
    if (fields.wind_gust.length) {
      datasets.push({
        label: `${label} — gust`,
        data: fields.wind_gust,
        borderColor: hexToRgba(color, 0.7),
        backgroundColor: 'transparent',
        borderWidth: 1,
        borderDash: [3, 4],
        pointRadius: 0,
        tension: 0.2,
      });
    }
  });

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        x: { ...CHART_DEFAULTS.scales.x, min: opts.xMin, max: opts.xMax },
        y: { ...CHART_DEFAULTS.scales.y, min: 0 },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Precipitation
// ---------------------------------------------------------------------------
function renderPrecipitationChart(points, fcSeries, opts, t) {
  const canvasId = 'chart-precipitation';
  const hasFcData = fcSeries.some(s => s.fields.precipitation.length > 0);
  const hasData = points.length > 0 || hasFcData;
  showCard('precipitation', hasData);
  if (!hasData) return;
  destroyChart(canvasId);

  const labelActual = t ? t('forecast_accuracy.legend_actual') : 'Actual';
  const barColor = CHART_COLORS.precipitation;
  const datasets = [
    {
      type: 'bar',
      label: labelActual,
      data: points,
      borderColor: barColor,
      backgroundColor: hexToRgba(barColor, 0.5),
      borderWidth: 0,
      barThickness: 'flex',
      yAxisID: 'y',
      order: 2,
    },
  ];

  fcSeries.forEach(({ label, color, fields }, idx) => {
    if (fields.precipitation.length) {
      datasets.push({
        type: 'bar',
        label,
        data: fields.precipitation,
        borderColor: color,
        backgroundColor: hexToRgba(color, 0.45),
        borderWidth: 1,
        barThickness: 'flex',
        yAxisID: 'y',
        order: 2 + idx,
      });
    }
  });

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        x: { ...CHART_DEFAULTS.scales.x, min: opts.xMin, max: opts.xMax },
        y: { type: 'linear', position: 'left', min: 0, ticks: { color: '#718096' }, grid: { color: '#1e2533' } },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Generic simple line chart
// ---------------------------------------------------------------------------
function renderSimpleChart(fieldName, points, fcSeries, opts, yOpts, t) {
  const canvasId = `chart-${fieldName}`;
  const hasFcData = fcSeries.some(s => s.fields[fieldName]?.length > 0);
  const hasData = points.length > 0 || hasFcData;
  showCard(fieldName, hasData);
  if (!hasData) return;
  destroyChart(canvasId);

  const labelActual = t ? t('forecast_accuracy.legend_actual') : 'Actual';
  const color = CHART_COLORS[fieldName] || '#a0aec0';
  const datasets = [
    {
      label: labelActual,
      data: points,
      borderColor: color,
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.2,
    },
  ];

  fcSeries.forEach(({ label, color: fcClr, fields }) => {
    const fPts = fields[fieldName] || [];
    if (fPts.length) {
      datasets.push({
        label,
        data: fPts,
        borderColor: fcClr,
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        borderDash: [6, 4],
        pointRadius: 0,
        tension: 0.2,
      });
    }
  });

  const yScale = { ticks: { color: '#718096' }, grid: { color: '#1e2533' } };
  if (yOpts.yMin !== undefined) yScale.min = yOpts.yMin;
  if (yOpts.yMax !== undefined) yScale.max = yOpts.yMax;

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        x: { ...CHART_DEFAULTS.scales.x, min: opts.xMin, max: opts.xMax },
        y: yScale,
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------
function showCard(fieldName, visible) {
  const card = document.getElementById(`card-${fieldName}`);
  if (card) card.style.display = visible ? '' : 'none';
}

function destroyChart(canvasId) {
  if (_charts[canvasId]) {
    _charts[canvasId].destroy();
    delete _charts[canvasId];
  }
}

function clearCharts() {
  Object.keys(_charts).forEach(id => destroyChart(id));
  document.getElementById('chartsGrid').style.display  = 'none';
  document.getElementById('legendStrip').style.display = 'none';
  document.getElementById('noDataNote').style.display  = 'none';
}

function showLoading(show) {
  document.getElementById('loadingOverlay').style.display = show ? 'block' : 'none';
}

function showError(msg) {
  const banner = document.getElementById('errorBanner');
  banner.textContent = msg;
  banner.style.display = 'block';
  showLoading(false);
}

function hideError() {
  document.getElementById('errorBanner').style.display = 'none';
}

function setDbDot(ok) {
  document.getElementById('dbDot').className = `dot ${ok ? 'green' : 'grey'}`;
}

function setRefreshLabel() {
  const t = typeof window.t === 'function' ? window.t : k => k;
  document.getElementById('lastRefresh').textContent =
    t('common.updated') + ' ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
