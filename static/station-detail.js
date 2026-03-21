/**
 * Lenticularis — station-detail.js
 *
 * Reads ?station_id= from the URL, fetches station metadata and history
 * from the API, then renders per-field Chart.js charts.
 */

// ---------------------------------------------------------------------------
// Config
// ---------------------------------------------------------------------------
const CHART_COLORS = {
  wind_speed:      '#63b3ed',
  wind_gust:       '#fc8181',
  wind_direction:  '#f6ad55',
  temperature:     '#f6ad55',
  humidity:        '#76e4f7',
  pressure:        '#b794f4',
  precipitation:   '#4299e1',
  snow_depth:      '#bee3f8',
};

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

// Draws a vertical "Now" line + amber shading for the forecast zone
const forecastZonePlugin = {
  id: 'forecastZone',
  beforeDraw(chart) {
    if (!chart.config.options?.plugins?.forecastZone?.enabled) return;
    const xScale = chart.scales?.x;
    if (!xScale) return;
    const { ctx, chartArea } = chart;
    const nowX = xScale.getPixelForValue(Date.now());
    if (nowX >= chartArea.left && nowX <= chartArea.right) {
      // Amber background for forecast area
      ctx.save();
      ctx.fillStyle = 'rgba(246, 173, 85, 0.06)';
      ctx.fillRect(nowX, chartArea.top, chartArea.right - nowX, chartArea.bottom - chartArea.top);
      ctx.restore();
    }
  },
  afterDraw(chart) {
    if (!chart.config.options?.plugins?.forecastZone?.enabled) return;
    const xScale = chart.scales?.x;
    if (!xScale) return;
    const { ctx, chartArea } = chart;
    const nowX = xScale.getPixelForValue(Date.now());
    if (nowX < chartArea.left || nowX > chartArea.right) return;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(nowX, chartArea.top);
    ctx.lineTo(nowX, chartArea.bottom);
    ctx.lineWidth = 1.5;
    ctx.strokeStyle = 'rgba(246, 173, 85, 0.8)';
    ctx.setLineDash([]);
    ctx.stroke();
    ctx.fillStyle = 'rgba(246, 173, 85, 0.9)';
    ctx.font = '10px sans-serif';
    ctx.fillText('Now', nowX + 4, chartArea.top + 12);
    ctx.restore();
  },
};

Chart.register(cursorGuidePlugin, forecastZonePlugin);

if (Chart.Tooltip?.positioners) {
  // eventPosition is the raw mouse {x, y} — use it directly instead of
  // averaging item.element.x values which snap to data point positions.
  Chart.Tooltip.positioners.topCursor = function topCursor(items, eventPosition) {
    if (!items || items.length === 0) return false;
    return {
      x: eventPosition.x,
      y: this.chart.chartArea.top + 8,
    };
  };
}

const FORECAST_COLOR = '#f6ad55'; // amber — visually distinct from all obs colors

function fcDataset(label, points, baseColor) {
  return {
    label,
    data: points,
    borderColor: FORECAST_COLOR,
    backgroundColor: hexToRgba(FORECAST_COLOR, 0.10),
    fill: false,
    borderWidth: 1.5,
    borderDash: [6, 4],
    pointRadius: 0,
    tension: 0.3,
  };
}

const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  plugins: {
    legend: { display: false },
    tooltip: {
      mode: 'index',
      intersect: false,
      position: 'topCursor',
      backgroundColor: '#1a1f2e',
      borderColor: '#2d3748',
      borderWidth: 1,
      titleColor: '#a0aec0',
      bodyColor: '#e2e8f0',
      callbacks: {
        // mode:'index' matches items by array index across datasets.
        // Observations (10-min) and forecast (1-h) have different lengths, so
        // index N in the forecast maps to index N in the obs array — a timestamp
        // from deep in the past. Fix: always show the label of the item with the
        // largest (= most future) x timestamp, which is always the forecast item
        // when the cursor is in the forecast region.
        title: (items) => {
          if (!items.length) return '';
          return items.reduce((a, b) => b.parsed.x > a.parsed.x ? b : a).label;
        },
      },
    },
    cursorGuide: { enabled: true },
    forecastZone: { enabled: false },
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
let _currentHours = 24;
let _showForecast = false;
const _charts = {};

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
document.addEventListener('DOMContentLoaded', () => {
  const params = new URLSearchParams(window.location.search);
  _stationId = params.get('station_id');

  if (!_stationId) {
    showError('No station_id provided in URL.');
    return;
  }

  loadStation();
});

// ---------------------------------------------------------------------------
// Load station metadata then history
// ---------------------------------------------------------------------------
async function loadStation() {
  try {
    const res = await fetch(`/api/stations/${encodeURIComponent(_stationId)}`);
    if (!res.ok) throw new Error(`HTTP ${res.status} fetching station metadata`);
    const station = await res.json();
    renderHeader(station);
  } catch (err) {
    showError(`Could not load station: ${err.message}`);
    return;
  }
  await loadHistory();
}

async function loadHistory() {
  showLoading(true);
  hideError();

  try {
    // In forecast mode always show last 48 h of observations
    const obsHours = _showForecast ? 48 : _currentHours;
    const [histRes, fcRes] = await Promise.all([
      fetch(`/api/stations/${encodeURIComponent(_stationId)}/history?hours=${obsHours}`),
      _showForecast
        ? fetch(`/api/stations/${encodeURIComponent(_stationId)}/forecast?hours=120`)
        : Promise.resolve(null),
    ]);
    if (!histRes.ok) throw new Error(`HTTP ${histRes.status}`);
    const histJson = await histRes.json();
    const fcJson   = fcRes?.ok ? await fcRes.json() : null;
    renderCharts(histJson.data || [], fcJson?.data || []);
    setRefreshLabel();
    setDbDot(true);
  } catch (err) {
    showError(`Failed to load history: ${err.message}`);
    setDbDot(false);
  } finally {
    showLoading(false);
  }
}

// ---------------------------------------------------------------------------
// Time-range selector
// ---------------------------------------------------------------------------
function setRange(hours) {
  _currentHours = hours;
  document.querySelectorAll('.range-btn[data-hours]').forEach(btn => {
    btn.classList.toggle('active', +btn.dataset.hours === hours);
  });
  loadHistory();
}

function toggleForecast() {
  _showForecast = !_showForecast;
  const btn = document.getElementById('forecastBtn');
  btn.classList.toggle('active', _showForecast);
  // Disable/enable range buttons while in forecast mode
  document.querySelectorAll('.range-btn[data-hours]').forEach(b => {
    b.disabled = _showForecast;
    b.style.opacity = _showForecast ? '0.4' : '';
  });
  loadHistory();
}

// ---------------------------------------------------------------------------
// Render header
// ---------------------------------------------------------------------------
function renderHeader(station) {
  document.title = `Lenticularis — ${station.name}`;
  document.getElementById('stationName').textContent = station.name;
  document.getElementById('stationId').textContent = station.station_id;

  const netBadge = document.getElementById('networkBadge');
  netBadge.textContent = station.network;
  netBadge.className = `network-badge network-${station.network || 'unknown'}`;

  const canton = document.getElementById('stationCanton');
  canton.textContent = station.canton ? window.t('common.canton', { name: station.canton }) : '';

  const elev = document.getElementById('stationElevation');
  elev.textContent = station.elevation != null ? window.t('common.elevation_asl', { m: station.elevation }) : '';
}

// ---------------------------------------------------------------------------
// Render all charts from history data (+ optional forecast rows)
// ---------------------------------------------------------------------------
function renderCharts(rows, fcRows = []) {
  const obsHours = _showForecast ? 48 : _currentHours;
  const now   = new Date();
  const xMin  = new Date(now.getTime() - obsHours * 3600000);
  const xMax  = _showForecast
    ? new Date(now.getTime() + 120 * 3600000)
    : now;

  function buildFields(data) {
    const f = { wind_speed:[], wind_gust:[], wind_direction:[], temperature:[],
                humidity:[], pressure_qnh:[], precipitation:[], snow_depth:[] };
    for (const row of data) {
      const t = row.timestamp;
      for (const field of Object.keys(f)) {
        if (row[field] != null) f[field].push({ x: t, y: row[field] });
      }
    }
    return f;
  }

  const fields   = buildFields(rows);
  const fcFields = buildFields(fcRows);

  const rangeOpts = { xMin, xMax, forecast: _showForecast };

  // Wind speed + gust (combined card)
  const hasWind = fields.wind_speed.length > 0 || fields.wind_gust.length > 0
               || fcFields.wind_speed.length > 0 || fcFields.wind_gust.length > 0;
  showCard('wind', hasWind);
  if (hasWind) renderWindChart(fields.wind_speed, fields.wind_gust, rangeOpts, fcFields.wind_speed, fcFields.wind_gust);

  // Wind direction — polar area heatmap rose + time-series line
  renderWindRoseChart(fields.wind_direction);
  renderWindDirLineChart(fields.wind_direction, rangeOpts, fcFields.wind_direction);

  // Temperature
  renderSimpleChart('temperature', fields.temperature, rangeOpts, fcFields.temperature);

  // Humidity
  renderSimpleChart('humidity', fields.humidity, { ...rangeOpts, yMin: 0, yMax: 100 }, fcFields.humidity);

  // Pressure
  renderSimpleChart('pressure', fields.pressure_qnh, rangeOpts, fcFields.pressure_qnh);

  // Precipitation (bar + cumulative line)
  renderPrecipitationChart(fields.precipitation, rangeOpts, fcFields.precipitation);

  // Snow depth
  renderSimpleChart('snow_depth', fields.snow_depth, { ...rangeOpts, yMin: 0 }, fcFields.snow_depth);

  // Show/hide the grid and no-data note
  const anyData = Object.values(fields).some(arr => arr.length > 0)
               || Object.values(fcFields).some(arr => arr.length > 0);
  document.getElementById('chartsGrid').style.display = anyData ? 'grid' : 'none';
  document.getElementById('noDataNote').style.display = anyData ? 'none' : 'block';
}

// ---------------------------------------------------------------------------
// Wind rose — radar chart bucketing readings into 16 compass directions
// ---------------------------------------------------------------------------
const WIND_ROSE_LABELS = ['N','NNO','NO','ONO','O','OSO','SO','SSO','S','SSW','SW','WSW','W','WNW','NW','NNW'];
// Rotate so N is centred at 12 o'clock: start = -90° - half-segment(11.25°)
const WIND_ROSE_START_ANGLE = (-Math.PI / 2) - (Math.PI / 16);

function renderWindRoseChart(points) {
  const canvasId = 'chart-wind_direction';
  showCard('wind_direction', points.length > 0);
  if (points.length === 0) return;

  destroyChart(canvasId);

  // Bucket each reading into the nearest of 16 directions (22.5° per bucket)
  const counts = new Array(16).fill(0);
  for (const pt of points) {
    const deg = ((pt.y % 360) + 360) % 360;
    const idx = Math.round(deg / 22.5) % 16;
    counts[idx]++;
  }

  const maxCount = Math.max(...counts, 1);
  const baseHex = CHART_COLORS.wind_direction;
  // Per-segment heatmap: opacity scales from 0.05 (no readings) to 0.90 (peak)
  const bgColors = counts.map(c => hexToRgba(baseHex, 0.05 + (c / maxCount) * 0.85));
  const borderColors = counts.map(c => hexToRgba(baseHex, 0.2 + (c / maxCount) * 0.8));

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'polarArea',
    data: {
      labels: WIND_ROSE_LABELS,
      datasets: [{
        label: 'Messungen',
        data: counts,
        backgroundColor: bgColors,
        borderColor: borderColors,
        borderWidth: 1.5,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      startAngle: WIND_ROSE_START_ANGLE,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: ctx => `${ctx.label}: ${ctx.parsed.r} readings`,
          },
        },
      },
      scales: {
        r: {
          min: 0,
          ticks: { color: '#718096', backdropColor: 'transparent', count: 4 },
          grid: { color: '#2d3748' },
          pointLabels: { display: true, color: '#a0aec0', font: { size: 10 } },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Wind direction — time-series line chart
// ---------------------------------------------------------------------------
function renderWindDirLineChart(points, opts = {}, fcPoints = []) {
  const canvasId = 'chart-wind_direction_line';
  const card = document.getElementById('card-wind_direction_line');
  const hasData = points.length > 0 || fcPoints.length > 0;
  if (card) card.style.display = hasData ? '' : 'none';
  if (!hasData) { destroyChart(canvasId); return; }

  destroyChart(canvasId);
  const color = CHART_COLORS.wind_direction;
  const compassLabels = { 0:'N', 45:'NO', 90:'O', 135:'SO', 180:'S', 225:'SW', 270:'W', 315:'NW', 360:'N' };

  const datasets = [{
    label: 'Wind direction (°)',
    data: points,
    borderColor: color,
    backgroundColor: 'transparent',
    borderWidth: 1.5,
    pointRadius: 1.5,
    pointBackgroundColor: color,
    tension: 0,
    spanGaps: false,
  }];
  if (fcPoints.length) datasets.push({ ...fcDataset('Forecast direction (°)', fcPoints), pointRadius: 0, tension: 0 });

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      plugins: { ...CHART_DEFAULTS.plugins, forecastZone: { enabled: !!opts.forecast } },
      scales: {
        x: { ...CHART_DEFAULTS.scales.x, ...(opts.xMin !== undefined ? { min: opts.xMin, max: opts.xMax } : {}) },
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
// Wind speed + gust — combined chart
// ---------------------------------------------------------------------------
function renderWindChart(speedPts, gustPts, opts = {}, fcSpeedPts = [], fcGustPts = []) {
  const canvasId = 'chart-wind';
  destroyChart(canvasId);

  const datasets = [
    {
      label: 'Wind speed (km/h)',
      data: speedPts,
      borderColor: CHART_COLORS.wind_speed,
      backgroundColor: 'transparent',
      borderWidth: 1.5,
      pointRadius: 0,
      tension: 0.2,
    },
    {
      label: 'Gust (km/h)',
      data: gustPts,
      borderColor: CHART_COLORS.wind_gust,
      backgroundColor: hexToRgba(CHART_COLORS.wind_gust, 0.12),
      fill: '-1',
      borderWidth: 1,
      borderDash: [4, 3],
      pointRadius: 0,
      tension: 0.2,
    },
  ];
  if (fcSpeedPts.length) datasets.push({ ...fcDataset('Forecast wind (km/h)', fcSpeedPts), fill: false });
  if (fcGustPts.length)  datasets.push({ ...fcDataset('Forecast gust (km/h)', fcGustPts), fill: false });

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        legend: { display: true, labels: { color: '#a0aec0', boxWidth: 12, font: { size: 11 } } },
        tooltip: CHART_DEFAULTS.plugins.tooltip,
        forecastZone: { enabled: !!opts.forecast },
      },
      scales: {
        x: { ...CHART_DEFAULTS.scales.x, ...(opts.xMin !== undefined ? { min: opts.xMin, max: opts.xMax } : {}) },
        y: { ...CHART_DEFAULTS.scales.y, min: 0 },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Precipitation — bars for individual readings + right-axis cumulative line
// ---------------------------------------------------------------------------
function renderPrecipitationChart(points, opts = {}, fcPoints = []) {
  const canvasId = 'chart-precipitation';
  const hasData = points.length > 0 || fcPoints.length > 0;
  showCard('precipitation', hasData);
  if (!hasData) return;

  destroyChart(canvasId);

  const barColor = CHART_COLORS.precipitation;
  const cumColor = '#90cdf4'; // lighter blue for cumulative line

  // Cumulative sum — API returns points in chronological order
  let cumSum = 0;
  const cumPoints = points.map(pt => {
    cumSum += pt.y ?? 0;
    return { x: pt.x, y: Math.round(cumSum * 10) / 10 };
  });

  const datasets = [
    {
      type: 'bar',
      label: 'Precipitation (mm)',
      data: points,
      borderColor: barColor,
      backgroundColor: hexToRgba(barColor, 0.5),
      borderWidth: 0,
      barThickness: 'flex',
      yAxisID: 'y',
      order: 2,
    },
    {
      type: 'line',
      label: 'Cumulative (mm)',
      data: cumPoints,
      borderColor: cumColor,
      backgroundColor: 'transparent',
      borderWidth: 2,
      pointRadius: 0,
      tension: 0.2,
      yAxisID: 'y1',
      order: 1,
    },
  ];
  if (fcPoints.length) {
    datasets.push({
      type: 'bar',
      label: 'Forecast precip (mm)',
      data: fcPoints,
      borderColor: FORECAST_COLOR,
      backgroundColor: hexToRgba(FORECAST_COLOR, 0.35),
      borderWidth: 0,
      barThickness: 'flex',
      yAxisID: 'y',
      order: 3,
    });
  }

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        legend: { display: true, labels: { color: '#a0aec0', boxWidth: 12, font: { size: 11 } } },
        tooltip: CHART_DEFAULTS.plugins.tooltip,
        forecastZone: { enabled: !!opts.forecast },
      },
      scales: {
        x: { ...CHART_DEFAULTS.scales.x, ...(opts.xMin !== undefined ? { min: opts.xMin, max: opts.xMax } : {}) },
        y:  { type: 'linear', position: 'left',  min: 0, ticks: { color: '#718096' }, grid: { color: '#1e2533' } },
        y1: { type: 'linear', position: 'right', min: 0, ticks: { color: cumColor }, grid: { drawOnChartArea: false } },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Generic simple chart renderer
// ---------------------------------------------------------------------------
function renderSimpleChart(fieldName, points, opts = {}, fcPoints = []) {
  const canvasId = `chart-${fieldName}`;
  const hasData = points.length > 0 || fcPoints.length > 0;
  showCard(fieldName, hasData);
  if (!hasData) return;

  destroyChart(canvasId);

  const chartType = opts.type || 'line';
  const isScatter = chartType === 'scatter';
  const isBar     = chartType === 'bar';
  const color     = CHART_COLORS[fieldName] || '#a0aec0';

  const yScale = { ticks: { color: '#718096' }, grid: { color: '#1e2533' } };
  if (opts.yMin !== undefined) yScale.min = opts.yMin;
  if (opts.yMax !== undefined) yScale.max = opts.yMax;
  if (opts.yTickLabels) {
    yScale.ticks = { ...yScale.ticks, callback: val => opts.yTickLabels[val] ?? val, stepSize: opts.yTickStep ?? 90 };
  }

  const datasets = [{
    label: fieldName,
    data: points,
    borderColor: color,
    backgroundColor: isBar ? hexToRgba(color, 0.5) : 'transparent',
    borderWidth: isScatter ? 0 : (isBar ? 0 : 1.5),
    pointRadius: isScatter ? 2 : 0,
    pointBackgroundColor: isScatter ? color : undefined,
    tension: isScatter || isBar ? 0 : 0.2,
    ...(opts.barThickness ? { barThickness: opts.barThickness } : {}),
  }];
  if (fcPoints.length) datasets.push(fcDataset(`Forecast ${fieldName}`, fcPoints, color));

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: chartType,
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      plugins: { ...CHART_DEFAULTS.plugins, forecastZone: { enabled: !!opts.forecast } },
      scales: {
        x: { ...CHART_DEFAULTS.scales.x, ...(opts.xMin !== undefined ? { min: opts.xMin, max: opts.xMax } : {}) },
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
  document.getElementById('lastRefresh').textContent =
    window.t('common.updated') + ' ' + new Date().toLocaleTimeString();
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
