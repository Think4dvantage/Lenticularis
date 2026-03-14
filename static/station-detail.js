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
  afterDraw(chart) {
    const xScale = chart.scales?.x;
    const yScale = chart.scales?.y;
    const tooltip = chart.tooltip;
    const active = tooltip?.getActiveElements?.() || [];

    if (!xScale || !yScale || active.length === 0) return;

    const x = active[0].element?.x;
    if (typeof x !== 'number') return;

    const { ctx } = chart;
    ctx.save();
    ctx.beginPath();
    ctx.moveTo(x, yScale.top);
    ctx.lineTo(x, yScale.bottom);
    ctx.lineWidth = 1;
    ctx.strokeStyle = 'rgba(226, 232, 240, 0.45)';
    ctx.setLineDash([4, 4]);
    ctx.stroke();
    ctx.restore();
  },
};

Chart.register(cursorGuidePlugin);

if (Chart.Tooltip?.positioners) {
  Chart.Tooltip.positioners.topCursor = function topCursor(items) {
    if (!items || items.length === 0) {
      return false;
    }

    // Keep tooltip aligned to hovered timestamp (x) but pinned near chart top.
    let x = 0;
    for (const item of items) {
      x += item.element.x;
    }
    x /= items.length;

    return {
      x,
      y: this.chart.chartArea.top + 8,
    };
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
    },
    cursorGuide: {
      enabled: true,
    },
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
    const res = await fetch(
      `/api/stations/${encodeURIComponent(_stationId)}/history?hours=${_currentHours}`
    );
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    const json = await res.json();
    renderCharts(json.data || []);
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
  document.querySelectorAll('.range-btn').forEach(btn => {
    btn.classList.toggle('active', +btn.dataset.hours === hours);
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
  canton.textContent = station.canton ? `Canton ${station.canton}` : '';

  const elev = document.getElementById('stationElevation');
  elev.textContent = station.elevation != null ? `${station.elevation} m a.s.l.` : '';
}

// ---------------------------------------------------------------------------
// Render all charts from history data
// ---------------------------------------------------------------------------
function renderCharts(rows) {
  const xMax = new Date();
  const xMin = new Date(xMax.getTime() - _currentHours * 60 * 60 * 1000);

  // Build per-field arrays
  const fields = {
    wind_speed:      [],
    wind_gust:       [],
    wind_direction:  [],
    temperature:     [],
    humidity:        [],
    pressure_qnh:    [],
    precipitation:   [],
    snow_depth:      [],
  };

  for (const row of rows) {
    const t = row.timestamp;
    for (const field of Object.keys(fields)) {
      if (row[field] != null) {
        fields[field].push({ x: t, y: row[field] });
      }
    }
  }

  const rangeOpts = { xMin, xMax };

  // Wind speed + gust (combined card)
  const hasWind = fields.wind_speed.length > 0 || fields.wind_gust.length > 0;
  showCard('wind', hasWind);
  if (hasWind) {
    renderWindChart(fields.wind_speed, fields.wind_gust, rangeOpts);
  }

  // Wind direction — polar area heatmap rose + time-series line
  renderWindRoseChart(fields.wind_direction);
  renderWindDirLineChart(fields.wind_direction, rangeOpts);

  // Temperature
  renderSimpleChart('temperature', fields.temperature, rangeOpts);

  // Humidity
  renderSimpleChart('humidity', fields.humidity, { ...rangeOpts, yMin: 0, yMax: 100 });

  // Pressure
  renderSimpleChart('pressure', fields.pressure_qnh, rangeOpts);

  // Precipitation (bar + cumulative line)
  renderPrecipitationChart(fields.precipitation, rangeOpts);

  // Snow depth
  renderSimpleChart('snow_depth', fields.snow_depth, { ...rangeOpts, yMin: 0 });

  // Show/hide the grid and no-data note
  const anyData = Object.values(fields).some(arr => arr.length > 0);
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
function renderWindDirLineChart(points, opts = {}) {
  const canvasId = 'chart-wind_direction_line';
  const card = document.getElementById('card-wind_direction_line');
  if (card) card.style.display = points.length > 0 ? '' : 'none';
  if (points.length === 0) { destroyChart(canvasId); return; }

  destroyChart(canvasId);
  const color = CHART_COLORS.wind_direction;

  const compassLabels = { 0:'N', 45:'NO', 90:'O', 135:'SO', 180:'S', 225:'SW', 270:'W', 315:'NW', 360:'N' };

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [{
        label: 'Wind direction (°)',
        data: points,
        borderColor: color,
        backgroundColor: 'transparent',
        borderWidth: 1.5,
        pointRadius: 1.5,
        pointBackgroundColor: color,
        tension: 0,
        spanGaps: false,
      }],
    },
    options: {
      ...CHART_DEFAULTS,
      scales: {
        x: {
          ...CHART_DEFAULTS.scales.x,
          ...(opts.xMin !== undefined ? { min: opts.xMin, max: opts.xMax } : {}),
        },
        y: {
          min: 0,
          max: 360,
          ticks: {
            color: '#718096',
            stepSize: 45,
            callback: val => compassLabels[val] ?? val,
          },
          grid: { color: '#1e2533' },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Wind speed + gust — combined chart
// ---------------------------------------------------------------------------
function renderWindChart(speedPts, gustPts, opts = {}) {
  const canvasId = 'chart-wind';
  destroyChart(canvasId);

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: 'line',
    data: {
      datasets: [
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
      ],
    },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        legend: { display: true, labels: { color: '#a0aec0', boxWidth: 12, font: { size: 11 } } },
        tooltip: CHART_DEFAULTS.plugins.tooltip,
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
function renderPrecipitationChart(points, opts = {}) {
  const canvasId = 'chart-precipitation';
  showCard('precipitation', points.length > 0);
  if (points.length === 0) return;

  destroyChart(canvasId);

  const barColor = CHART_COLORS.precipitation;
  const cumColor = '#90cdf4'; // lighter blue for cumulative line

  // Cumulative sum — API returns points in chronological order
  let cumSum = 0;
  const cumPoints = points.map(pt => {
    cumSum += pt.y ?? 0;
    return { x: pt.x, y: Math.round(cumSum * 10) / 10 };
  });

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    data: {
      datasets: [
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
      ],
    },
    options: {
      ...CHART_DEFAULTS,
      plugins: {
        ...CHART_DEFAULTS.plugins,
        legend: { display: true, labels: { color: '#a0aec0', boxWidth: 12, font: { size: 11 } } },
        tooltip: CHART_DEFAULTS.plugins.tooltip,
      },
      scales: {
        x: {
          ...CHART_DEFAULTS.scales.x,
          ...(opts.xMin !== undefined ? { min: opts.xMin, max: opts.xMax } : {}),
        },
        y: {
          type: 'linear',
          position: 'left',
          min: 0,
          ticks: { color: '#718096' },
          grid: { color: '#1e2533' },
        },
        y1: {
          type: 'linear',
          position: 'right',
          min: 0,
          ticks: { color: cumColor },
          grid: { drawOnChartArea: false },
        },
      },
    },
  });
}

// ---------------------------------------------------------------------------
// Generic simple chart renderer
// ---------------------------------------------------------------------------
function renderSimpleChart(fieldName, points, opts = {}) {
  const cardId = `card-${fieldName}`;
  const canvasId = `chart-${fieldName}`;
  showCard(fieldName, points.length > 0);
  if (points.length === 0) return;

  destroyChart(canvasId);

  const chartType = opts.type || 'line';
  const isScatter = chartType === 'scatter';
  const isBar = chartType === 'bar';

  const yScale = {
    ticks: { color: '#718096' },
    grid: { color: '#1e2533' },
  };
  if (opts.yMin !== undefined) yScale.min = opts.yMin;
  if (opts.yMax !== undefined) yScale.max = opts.yMax;
  if (opts.yTickLabels) {
    yScale.ticks = {
      ...yScale.ticks,
      callback: val => opts.yTickLabels[val] ?? val,
      stepSize: opts.yTickStep ?? 90,
    };
  }

  const color = CHART_COLORS[fieldName] || '#a0aec0';

  const ctx = document.getElementById(canvasId).getContext('2d');
  _charts[canvasId] = new Chart(ctx, {
    type: chartType,
    data: {
      datasets: [{
        label: fieldName,
        data: points,
        borderColor: color,
        backgroundColor: isBar ? hexToRgba(color, 0.5) : 'transparent',
        borderWidth: isScatter ? 0 : (isBar ? 0 : 1.5),
        pointRadius: isScatter ? 2 : 0,
        pointBackgroundColor: isScatter ? color : undefined,
        tension: isScatter || isBar ? 0 : 0.2,
        ...(opts.barThickness ? { barThickness: opts.barThickness } : {}),
      }],
    },
    options: {
      ...CHART_DEFAULTS,
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
    'Updated ' + new Date().toLocaleTimeString();
}

function hexToRgba(hex, alpha) {
  const r = parseInt(hex.slice(1, 3), 16);
  const g = parseInt(hex.slice(3, 5), 16);
  const b = parseInt(hex.slice(5, 7), 16);
  return `rgba(${r},${g},${b},${alpha})`;
}
