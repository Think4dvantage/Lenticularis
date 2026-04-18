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
  wind_speed:      '#63b3ed',  // blue
  wind_gust:       '#fc8181',  // red
  wind_direction:  '#4fd1c5',  // teal (distinct from forecast amber)
  temperature:     '#fc8181',  // salmon (distinct from forecast amber)
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

// Zone-aware tooltip filter: show obs items only in the past zone, forecast items only
// in the future zone. Prevents index-mismatched values from appearing in the wrong region.
function makeForecastFilter(hasForecast) {
  if (!hasForecast) return undefined;
  return (item) => {
    // Ensemble band anchors are folded into the probable line's label — hide them here
    const lbl = item.dataset.label ?? '';
    if (lbl.endsWith('(min)') || lbl.endsWith('(max)')) return false;
    const isFc = item.dataset.isForecast === true;
    return isFc ? item.parsed.x > Date.now() : item.parsed.x <= Date.now();
  };
}

/**
 * Returns 1 or 3 Chart.js dataset objects for a forecast series.
 *
 * When minPts / maxPts are supplied (SwissMeteo ensemble), the function emits:
 *   [0] invisible min line  — anchor for the fill
 *   [1] invisible max line  — fills down to [0] with low opacity (the band)
 *   [2] solid probable line — the main forecast value
 *
 * When no ensemble data is available (Open-Meteo), a single dashed line is
 * returned, matching the original behaviour.
 */
function fcDatasets(label, pts, minPts, maxPts, color = FORECAST_COLOR) {
  const hasEnsemble = minPts?.length > 0 && maxPts?.length > 0;
  const base = { pointRadius: 0, tension: 0.3, spanGaps: true, isForecast: true };

  if (hasEnsemble) {
    return [
      // min — invisible anchor
      { ...base, label: `${label} (min)`, data: minPts,
        borderColor: 'transparent', backgroundColor: 'transparent', fill: false, borderWidth: 0 },
      // max — fills down to min with a low-opacity band
      { ...base, label: `${label} (max)`, data: maxPts,
        borderColor: hexToRgba(color, 0.4), backgroundColor: hexToRgba(color, 0.15),
        fill: '-1', borderWidth: 1, borderDash: [3, 3] },
      // probable — solid line on top
      { ...base, label, data: pts,
        borderColor: color, backgroundColor: 'transparent',
        fill: false, borderWidth: 2 },
    ];
  }

  // No ensemble data — single dashed line (Open-Meteo style)
  return [{
    ...base, label, data: pts,
    borderColor: color, backgroundColor: hexToRgba(color, 0.10),
    fill: false, borderWidth: 1.5, borderDash: [6, 4],
  }];
}

const CHART_DEFAULTS = {
  responsive: true,
  maintainAspectRatio: false,
  animation: false,
  plugins: {
    legend: { display: false },
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
        label: (item) => {
          const dsLabel = item.dataset.label ?? '';
          const v = item.parsed.y;
          if (v == null || isNaN(v)) return `${dsLabel}: –`;
          const base = Math.round(v * 10) / 10;
          // If ensemble min/max sibling datasets exist, append the range inline.
          // Match by timestamp (not dataIndex) — min/max arrays may be shorter
          // than the probable array when some rows lack ensemble fields.
          const datasets = item.chart?.data?.datasets ?? [];
          const minDs = datasets.find(d => d.label === `${dsLabel} (min)`);
          const maxDs = datasets.find(d => d.label === `${dsLabel} (max)`);
          const ts    = item.parsed.x;
          const minPt = minDs?.data?.find(p => new Date(p.x).getTime() === ts);
          const maxPt = maxDs?.data?.find(p => new Date(p.x).getTime() === ts);
          const minV  = minPt?.y;
          const maxV  = maxPt?.y;
          if (minV != null && !isNaN(minV) && maxV != null && !isNaN(maxV)) {
            return `${dsLabel}: ${base}  [${Math.round(minV * 10) / 10} – ${Math.round(maxV * 10) / 10}]`;
          }
          return `${dsLabel}: ${base}`;
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
    renderForecastSourceBadge(fcJson);
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
  if (!_showForecast) {
    const badge = document.getElementById('forecastSourceBadge');
    if (badge) badge.style.display = 'none';
  }
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
    const f = {
      wind_speed:[], wind_speed_min:[], wind_speed_max:[],
      wind_gust:[], wind_gust_min:[], wind_gust_max:[],
      wind_direction:[],
      temperature:[], temperature_min:[], temperature_max:[],
      humidity:[], humidity_min:[], humidity_max:[],
      pressure_qff:[], pressure_qff_min:[], pressure_qff_max:[],
      precipitation:[], precipitation_min:[], precipitation_max:[],
      snow_depth:[],
    };
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
  if (hasWind) renderWindChart(
    fields.wind_speed, fields.wind_gust, rangeOpts,
    fcFields.wind_speed, fcFields.wind_gust,
    fcFields.wind_speed_min, fcFields.wind_speed_max,
    fcFields.wind_gust_min,  fcFields.wind_gust_max,
  );

  // Wind direction — polar area heatmap rose + time-series line
  renderWindRoseChart(fields.wind_direction, fcFields.wind_direction);
  renderWindDirLineChart(fields.wind_direction, rangeOpts, fcFields.wind_direction);

  // Temperature
  renderSimpleChart('temperature', fields.temperature, rangeOpts, fcFields.temperature, fcFields.temperature_min, fcFields.temperature_max);

  // Humidity
  renderSimpleChart('humidity', fields.humidity, { ...rangeOpts, yMin: 0, yMax: 100 }, fcFields.humidity, fcFields.humidity_min, fcFields.humidity_max);

  // Pressure
  renderSimpleChart('pressure', fields.pressure_qff, rangeOpts, fcFields.pressure_qff, fcFields.pressure_qff_min, fcFields.pressure_qff_max);

  // Precipitation (bar + cumulative line)
  renderPrecipitationChart(fields.precipitation, rangeOpts, fcFields.precipitation, fcFields.precipitation_min, fcFields.precipitation_max);

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

function renderWindRoseChart(obsPts, fcPts = []) {
  const canvasId = 'chart-wind_direction';
  const hasObs = obsPts.length > 0;
  const hasFc  = fcPts.length > 0;
  showCard('wind_direction', hasObs || hasFc);
  if (!hasObs && !hasFc) return;

  destroyChart(canvasId);

  function bucket(pts) {
    const counts = new Array(16).fill(0);
    for (const pt of pts) {
      const deg = ((pt.y % 360) + 360) % 360;
      counts[Math.round(deg / 22.5) % 16]++;
    }
    return counts;
  }

  const obsCounts = bucket(obsPts);
  const fcCounts  = bucket(fcPts);
  const maxObs = Math.max(...obsCounts, 1);
  const maxFc  = Math.max(...fcCounts, 1);

  const obsColor = CHART_COLORS.wind_direction; // teal
  const fcColor  = FORECAST_COLOR;              // amber

  const datasets = [];
  if (hasObs) {
    datasets.push({
      label: 'Observed direction',
      data: obsCounts,
      backgroundColor: obsCounts.map(c => hexToRgba(obsColor, 0.08 + (c / maxObs) * 0.82)),
      borderColor:     obsCounts.map(c => hexToRgba(obsColor, 0.25 + (c / maxObs) * 0.75)),
      borderWidth: 1.5,
    });
  }
  if (hasFc) {
    datasets.push({
      label: 'Forecast direction',
      data: fcCounts,
      backgroundColor: fcCounts.map(c => hexToRgba(fcColor, 0.06 + (c / maxFc) * 0.50)),
      borderColor:     fcCounts.map(c => hexToRgba(fcColor, 0.20 + (c / maxFc) * 0.55)),
      borderWidth: 1,
    });
  }

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'polarArea',
    data: { labels: WIND_ROSE_LABELS, datasets },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      animation: false,
      startAngle: WIND_ROSE_START_ANGLE,
      plugins: {
        legend: {
          display: hasFc,
          labels: {
            color: '#a0aec0', boxWidth: 10, font: { size: 10 },
            // PolarArea normally generates one entry per data label (16 sectors).
            // Override to produce one entry per dataset instead.
            generateLabels: (chart) => chart.data.datasets.map((ds, i) => ({
              text: ds.label,
              fillStyle: Array.isArray(ds.backgroundColor)
                ? ds.backgroundColor[ds.data.indexOf(Math.max(...ds.data))]
                : ds.backgroundColor,
              strokeStyle: Array.isArray(ds.borderColor)
                ? ds.borderColor[ds.data.indexOf(Math.max(...ds.data))]
                : ds.borderColor,
              lineWidth: ds.borderWidth,
              hidden: !chart.isDatasetVisible(i),
              datasetIndex: i,
            })),
          },
        },
        tooltip: {
          callbacks: {
            label: (item) => `${item.dataset.label} — ${item.label}: ${item.parsed.r}`,
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
  if (fcPoints.length) datasets.push(...fcDatasets('Forecast direction (°)', fcPoints, [], []).map(d => ({ ...d, tension: 0 })));

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
const FC_WIND_COLOR = '#9f7aea'; // purple — forecast wind speed
const FC_GUST_COLOR = '#ecc94b'; // yellow — forecast gust

function renderWindChart(speedPts, gustPts, opts = {}, fcSpeedPts = [], fcGustPts = [], fcSpeedMin = [], fcSpeedMax = [], fcGustMin = [], fcGustMax = []) {
  const canvasId = 'chart-wind';
  destroyChart(canvasId);

  const hasForecast = fcSpeedPts.length > 0 || fcGustPts.length > 0;

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
  if (fcSpeedPts.length) datasets.push(...fcDatasets('Forecast wind (km/h)', fcSpeedPts, fcSpeedMin, fcSpeedMax, FC_WIND_COLOR));
  if (fcGustPts.length)  datasets.push(...fcDatasets('Forecast gust (km/h)', fcGustPts, fcGustMin, fcGustMax, FC_GUST_COLOR));

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        legend: { display: true, labels: {
          color: '#a0aec0', boxWidth: 12, font: { size: 11 },
          // Hide the invisible min-anchor datasets from the legend
          filter: item => !item.text.endsWith('(min)'),
        }},
        tooltip: {
          ...CHART_DEFAULTS.plugins.tooltip,
          filter: makeForecastFilter(hasForecast),
        },
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
function renderPrecipitationChart(points, opts = {}, fcPoints = [], fcMinPts = [], fcMaxPts = []) {
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
      isForecast: true,
    });
    // Ensemble band as line overlay (bars don't support fill bands natively)
    if (fcMinPts.length && fcMaxPts.length) {
      datasets.push(
        { type: 'line', label: 'Forecast precip min', data: fcMinPts,
          borderColor: 'transparent', backgroundColor: 'transparent', fill: false,
          borderWidth: 0, pointRadius: 0, tension: 0.3, yAxisID: 'y', order: 4, isForecast: true },
        { type: 'line', label: 'Forecast precip max', data: fcMaxPts,
          borderColor: hexToRgba(FORECAST_COLOR, 0.5), backgroundColor: hexToRgba(FORECAST_COLOR, 0.12),
          fill: '-1', borderWidth: 1, borderDash: [3, 3], pointRadius: 0, tension: 0.3, yAxisID: 'y', order: 4, isForecast: true },
      );
    }
  }

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        legend: { display: true, labels: { color: '#a0aec0', boxWidth: 12, font: { size: 11 } } },
        tooltip: {
          ...CHART_DEFAULTS.plugins.tooltip,
          filter: makeForecastFilter(fcPoints.length > 0),
        },
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
function renderSimpleChart(fieldName, points, opts = {}, fcPoints = [], fcMinPts = [], fcMaxPts = []) {
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
  const fcLabel = `Forecast ${fieldName}`;
  if (fcPoints.length) datasets.push(...fcDatasets(fcLabel, fcPoints, fcMinPts, fcMaxPts));

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: chartType,
    data: { datasets },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        legend: fcPoints.length ? {
          display: true,
          labels: { color: '#a0aec0', boxWidth: 12, font: { size: 11 },
            filter: item => !item.text.endsWith('(min)') },
        } : { display: false },
        tooltip: {
          ...CHART_DEFAULTS.plugins.tooltip,
          filter: makeForecastFilter(fcPoints.length > 0),
        },
        forecastZone: { enabled: !!opts.forecast },
      },
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
    window.t('common.updated') + ' ' + new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit', hour12: false });
}

function renderForecastSourceBadge(fcJson) {
  const el = document.getElementById('forecastSourceBadge');
  if (!el) return;
  if (!fcJson || !_showForecast) {
    el.style.display = 'none';
    return;
  }
  const source = fcJson.forecast_source || '—';
  const model  = fcJson.forecast_model  || '—';
  el.textContent = `${model} · ${source}`;
  el.style.display = '';
}

function openAccuracy() {
  if (_stationId) {
    window.location.href = `/forecast-accuracy?station_id=${encodeURIComponent(_stationId)}`;
  }
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
