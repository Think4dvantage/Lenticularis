/**
 * Lenticularis — forecast-analysis.js
 *
 * Fetches the cross-station forecast accuracy ranking from
 * GET /api/stations/forecast-accuracy-ranking and renders per-field
 * ranked tables with MAE, bias, and correction hints, broken down by
 * lead-time bucket (D+1 / D+2 / D+3).
 */

// ---------------------------------------------------------------------------
// Constants
// ---------------------------------------------------------------------------

const DAYS = 90;
const TOP_N = 10;

const FIELD_ORDER = [
  'wind_speed',
  'wind_gust',
  'wind_direction',
  'temperature',
  'humidity',
  'pressure_qff',
  'precipitation',
];

// Thresholds for MAE colour classification (per field, high/medium boundary)
const MAE_HIGH_THRESHOLD = {
  wind_speed:     5.0,
  wind_gust:      8.0,
  wind_direction: 30.0,
  temperature:    3.0,
  humidity:       15.0,
  pressure_qff:   5.0,
  precipitation:  2.0,
};
const MAE_MEDIUM_THRESHOLD = {
  wind_speed:     2.5,
  wind_gust:      4.0,
  wind_direction: 15.0,
  temperature:    1.5,
  humidity:       8.0,
  pressure_qff:   2.5,
  precipitation:  0.8,
};

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------

let _ranking = null;
let _activeBucket = 'D+1';

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function t(key, vars) {
  const fn = typeof window.t === 'function' ? window.t : k => k;
  return fn(key, vars);
}

function escHtml(s) {
  return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
    ({ '&':'&amp;', '<':'&lt;', '>':'&gt;', '"':'&quot;', "'":'&#39;' }[c]));
}

function showError(msg) {
  const el = document.getElementById('errorBanner');
  el.textContent = msg;
  el.style.display = 'block';
  console.error('[App:forecast-analysis] Error banner shown:', msg);
}

function clearError() {
  document.getElementById('errorBanner').style.display = 'none';
}

let _loadingTimer = null;

function setLoading(on) {
  document.getElementById('loadingOverlay').style.display = on ? '' : 'none';
  document.getElementById('sectionsWrapper').style.display = on ? 'none' : '';
  document.getElementById('refreshBtn').disabled = on;
  console.log('[App:forecast-analysis] loading state:', on);

  const hint = document.getElementById('loadingHint');
  if (on) {
    hint.textContent = '';
  } else {
    clearTimeout(_loadingTimer);
    hint.textContent = '';
  }
}

function maeClass(field, mae) {
  if (mae >= MAE_HIGH_THRESHOLD[field])   return 'mae-high';
  if (mae >= MAE_MEDIUM_THRESHOLD[field]) return 'mae-medium';
  return 'mae-low';
}

function fmtNumber(val, decimals) {
  return val.toFixed(decimals);
}

function correctionHint(field, bias, mae) {
  if (Math.abs(bias) < 0.1 * mae) {
    return { cls: '', text: t('forecast_analysis.correction_neutral') };
  }
  const decimals = field === 'wind_direction' ? 0 : 1;
  const absVal = fmtNumber(Math.abs(bias), decimals);
  if (bias > 0) {
    return { cls: 'correction-high', text: t('forecast_analysis.correction_high', { val: absVal }) };
  }
  return { cls: 'correction-low', text: t('forecast_analysis.correction_low', { val: absVal }) };
}

function networkBadgeHtml(network) {
  const safe = (network || 'unknown').toLowerCase().replace(/[^a-z0-9]/g, '');
  const cls = `network-badge network-${safe}`;
  return `<span class="${cls}">${network || '—'}</span>`;
}

// ---------------------------------------------------------------------------
// Render
// ---------------------------------------------------------------------------

function renderSections(ranking, bucket) {
  const wrapper = document.getElementById('sectionsWrapper');
  wrapper.innerHTML = '';
  console.log('[App:forecast-analysis] rendering sections for bucket:', bucket);

  for (const field of FIELD_ORDER) {
    const fieldData = ranking[field];
    if (!fieldData) continue;

    const rows = (fieldData[bucket] || []);
    const fieldLabel = t(`forecast_analysis.fields.${field}`);
    const decimals = (field === 'wind_direction') ? 0 : (field === 'pressure_qff' ? 1 : 1);

    const section = document.createElement('div');
    section.className = 'field-section';

    const header = document.createElement('div');
    header.className = 'field-section-header';
    header.textContent = fieldLabel;
    section.appendChild(header);

    const table = document.createElement('table');
    table.className = 'rank-table';

    // Header row
    const thead = document.createElement('thead');
    thead.innerHTML = `<tr>
      <th data-i18n="forecast_analysis.col_rank">${t('forecast_analysis.col_rank')}</th>
      <th data-i18n="forecast_analysis.col_station">${t('forecast_analysis.col_station')}</th>
      <th data-i18n="forecast_analysis.col_network">${t('forecast_analysis.col_network')}</th>
      <th data-i18n="forecast_analysis.col_canton">${t('forecast_analysis.col_canton')}</th>
      <th data-i18n="forecast_analysis.col_mae">${t('forecast_analysis.col_mae')}</th>
      <th data-i18n="forecast_analysis.col_bias">${t('forecast_analysis.col_bias')}</th>
      <th data-i18n="forecast_analysis.col_samples">${t('forecast_analysis.col_samples')}</th>
      <th data-i18n="forecast_analysis.col_correction">${t('forecast_analysis.col_correction')}</th>
    </tr>`;
    table.appendChild(thead);

    const tbody = document.createElement('tbody');

    if (rows.length === 0) {
      const tr = document.createElement('tr');
      tr.className = 'no-data-row';
      tr.innerHTML = `<td colspan="8">${t('forecast_analysis.no_data')}</td>`;
      tbody.appendChild(tr);
    } else {
      rows.forEach((entry, idx) => {
        const rank = idx + 1;
        const rankCls = rank <= 3 ? `rank-${rank}` : '';
        const maeCls = maeClass(field, entry.mae);
        const biasSign = entry.bias > 0 ? '+' : '';
        const biasCls = entry.bias > 0 ? 'bias-pos' : (entry.bias < 0 ? 'bias-neg' : 'bias-cell');
        const hint = correctionHint(field, entry.bias, entry.mae);
        const stationUrl = `/forecast-accuracy?station=${encodeURIComponent(entry.station_id)}`;

        const tr = document.createElement('tr');
        tr.innerHTML = `
          <td><span class="rank-num ${rankCls}">${rank}</span></td>
          <td><a class="station-link" href="${stationUrl}">${escHtml(entry.name || entry.station_id)}</a></td>
          <td>${networkBadgeHtml(entry.network)}</td>
          <td>${entry.canton || '—'}</td>
          <td><span class="mae-cell ${maeCls}">${fmtNumber(entry.mae, decimals)}</span></td>
          <td><span class="${biasCls}">${biasSign}${fmtNumber(entry.bias, decimals)}</span></td>
          <td class="samples-cell">${entry.n}</td>
          <td><span class="correction-hint ${hint.cls}">${hint.text}</span></td>
        `;
        tbody.appendChild(tr);
      });
    }

    table.appendChild(tbody);
    section.appendChild(table);
    wrapper.appendChild(section);
  }

  console.log('[App:forecast-analysis] sections rendered, fields:', FIELD_ORDER.length);
}

// ---------------------------------------------------------------------------
// Data loading
// ---------------------------------------------------------------------------

async function loadRanking() {
  clearError();
  setLoading(true);

  const url = `/api/stations/forecast-accuracy-ranking?days=${DAYS}&top_n=${TOP_N}`;
  const t0 = performance.now();
  console.log('[App:forecast-analysis] fetch start:', url);

  try {
    const resp = await fetch(url);
    const elapsed = Math.round(performance.now() - t0);

    if (!resp.ok) {
      const msg = `API error ${resp.status}`;
      console.error('[App:forecast-analysis] fetch failed:', msg, 'elapsed:', elapsed, 'ms');
      showError(msg);
      setLoading(false);
      return;
    }

    const data = await resp.json();
    console.log(
      '[App:forecast-analysis] fetch ok, elapsed:', elapsed, 'ms,',
      'computed_at:', data.computed_at,
      'days:', data.days,
      'fields:', Object.keys(data.ranking || {}).length
    );

    _ranking = data.ranking;

    const badge = document.getElementById('basedOnBadge');
    badge.textContent = t('forecast_analysis.based_on', { days: data.days });

    renderSections(_ranking, _activeBucket);
  } catch (err) {
    console.error('[App:forecast-analysis] fetch error:', err);
    showError(String(err));
  } finally {
    setLoading(false);
  }
}

// ---------------------------------------------------------------------------
// Controls
// ---------------------------------------------------------------------------

function setBucket(bucket) {
  if (_activeBucket === bucket && _ranking) return;
  _activeBucket = bucket;
  console.log('[App:forecast-analysis] bucket selected:', bucket);

  document.querySelectorAll('.toggle-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.bucket === bucket);
  });

  if (_ranking) {
    renderSections(_ranking, bucket);
  }
}

// ---------------------------------------------------------------------------
// Init (called from inline module script after initI18n)
// ---------------------------------------------------------------------------

function initPage() {
  console.log('[App:forecast-analysis] init — DAYS:', DAYS, 'TOP_N:', TOP_N);
  loadRanking();
}
