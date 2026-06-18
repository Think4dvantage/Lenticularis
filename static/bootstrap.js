/**
 * bootstrap.js — shared page bootstrap helpers for Lenticularis.
 *
 * Provides:
 *   renderNav(mount)            — inject the standard nav into a mount element
 *   bootstrapPage(opts)         — renderNav + initI18n + renderNavAuth + renderLangPicker
 *
 * Usage (in a page module script):
 *   import { bootstrapPage } from '/static/bootstrap.js';
 *   await bootstrapPage({ page: 'stations' });
 *
 * Pages with a non-standard nav (stations: extra status dot; admin: extra nav link)
 * keep their own inline <nav> and are not yet migrated — see T22 notes.
 */

import { initI18n, renderLangPicker } from '/static/i18n.js';
import { renderNavAuth } from '/static/auth.js';

const _NAV_HTML = `<nav class="top-nav">
  <a href="/" class="nav-brand" id="navBrand">🪁 Lenticularis</a>
  <div class="nav-links" id="navLinks">
    <a href="/" class="nav-link" data-i18n="nav.map">Map</a>
    <a href="/stations" class="nav-link" data-i18n="nav.stations">Stations</a>
    <a href="/rulesets" class="nav-link" data-i18n="nav.rulesets">Rule Sets</a>
    <a href="/stats" class="nav-link" data-i18n="nav.stats">Statistics</a>
    <a href="/foehn" class="nav-link" data-i18n="nav.foehn">Föhn</a>
    <a href="/wind-forecast" class="nav-link" data-i18n="nav.wind_forecast">Wind Forecast</a>
    <a href="/forecast-analysis" class="nav-link" data-i18n="forecast_analysis.title">Forecast Analysis</a>
    <a href="/help" class="nav-link" data-i18n="nav.help">Help</a>
  </div>
  <div id="navLangPicker"></div>
  <div class="nav-user" id="navUser"></div>
</nav>`;

/**
 * Inject the standard nav into a mount element and mark the current-page link active.
 * @param {HTMLElement} mount
 */
export function renderNav(mount) {
  mount.innerHTML = _NAV_HTML;
  const path = window.location.pathname;
  mount.querySelectorAll('.nav-link').forEach(a => {
    const href = a.getAttribute('href');
    if (href && (path === href || (href !== '/' && path.startsWith(href)))) {
      a.classList.add('active');
    }
  });
}

/**
 * Bootstrap a page: inject nav, run i18n, mount auth widget and lang picker.
 *
 * @param {object}      [opts]
 * @param {string}      [opts.page]      - Page name passed to renderNavAuth (e.g. 'rulesets').
 * @param {HTMLElement} [opts.navMount]  - Override the nav mount point (default: #appNav).
 */
export async function bootstrapPage(opts = {}) {
  const mount = opts.navMount || document.getElementById('appNav');
  if (mount) renderNav(mount);
  await initI18n();
  renderNavAuth(opts.page || '');
  renderLangPicker();
}
