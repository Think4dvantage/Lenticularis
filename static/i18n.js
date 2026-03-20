/**
 * Lenticularis i18n — lightweight vanilla JS translation engine
 *
 * Usage (in ES module scripts):
 *   import { initI18n, t, renderLangPicker, setLanguage } from '/static/i18n.js';
 *   await initI18n();
 *   renderLangPicker();
 *
 * Usage in non-module / inline scripts:
 *   window.t('nav.map')                        → "Map" (or translated equivalent)
 *   window.setLanguage('de')                   → persists + reloads
 */

const SUPPORTED = ['en', 'de', 'fr', 'it'];
const LANG_LABELS = { en: 'EN', de: 'DE', fr: 'FR', it: 'IT' };
const STORAGE_KEY = 'lenti_lang';

let _strings = {};
let _lang = 'en';

// Synchronous fallback so non-module scripts calling window.t() before init
// get a readable string instead of undefined.
window.t = (key) => {
  const seg = key.split('.');
  return seg[seg.length - 1].replace(/_/g, ' ');
};
window.setLanguage = setLanguage;

// ---------------------------------------------------------------------------
// Public API
// ---------------------------------------------------------------------------

/**
 * Load the appropriate locale, set window.t, apply data-i18n attributes.
 * Must be awaited before rendering any translated content.
 * @returns {Promise<string>} resolved language code
 */
export async function initI18n() {
  _lang = _detectLanguage();
  await _loadStrings(_lang);

  // Expose globally for non-module scripts
  window.t = t;
  window.setLanguage = setLanguage;
  window.getI18nLang = () => _lang;

  document.documentElement.lang = _lang;
  applyDataI18n();
  return _lang;
}

/**
 * Translate a dotted key, optionally interpolating {variable} placeholders.
 * Falls back to the key itself if the string is not found.
 * @param {string} key  Dotted path, e.g. 'nav.map' or 'stations.n_stations'
 * @param {Object} vars Substitution variables, e.g. { n: 5 }
 */
export function t(key, vars = {}) {
  const val = key.split('.').reduce(
    (o, k) => (o && typeof o === 'object' ? o[k] : undefined),
    _strings
  );
  if (val == null) return key;
  return String(val).replace(/\{(\w+)\}/g, (_, k) => (k in vars ? vars[k] : `{${k}}`));
}

/**
 * Persist language choice and reload.
 * @param {string} lang  e.g. 'de'
 */
export function setLanguage(lang) {
  if (!SUPPORTED.includes(lang)) return;
  localStorage.setItem(STORAGE_KEY, lang);
  location.reload();
}

export function getLanguage() { return _lang; }

/**
 * Replace all [data-i18n] / [data-i18n-placeholder] / [data-i18n-title]
 * elements with translated text.  Call again if new DOM is inserted.
 */
export function applyDataI18n() {
  document.querySelectorAll('[data-i18n]').forEach(el => {
    el.textContent = t(el.dataset.i18n);
  });
  document.querySelectorAll('[data-i18n-placeholder]').forEach(el => {
    el.placeholder = t(el.dataset.i18nPlaceholder);
  });
  document.querySelectorAll('[data-i18n-title]').forEach(el => {
    el.title = t(el.dataset.i18nTitle);
  });
}

/**
 * Inject a language-picker widget into the element with the given id.
 * Typically called after initI18n() and renderNavAuth().
 * @param {string} containerId  Default: 'navLangPicker'
 */
export function renderLangPicker(containerId = 'navLangPicker') {
  const container = document.getElementById(containerId);
  if (!container) return;
  container.innerHTML = SUPPORTED.map(lang =>
    `<button class="lang-btn${lang === _lang ? ' active' : ''}" onclick="setLanguage('${lang}')">${LANG_LABELS[lang]}</button>`
  ).join('');
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function _detectLanguage() {
  // 1. Explicit URL override (?lang=de)
  const urlLang = new URLSearchParams(location.search).get('lang');
  if (urlLang && SUPPORTED.includes(urlLang)) return urlLang;
  // 2. User preference persisted in localStorage
  const stored = localStorage.getItem(STORAGE_KEY);
  if (stored && SUPPORTED.includes(stored)) return stored;
  // 3. Browser language (first two chars)
  const browser = (navigator.language || 'en').slice(0, 2).toLowerCase();
  return SUPPORTED.includes(browser) ? browser : 'en';
}

async function _loadStrings(lang) {
  try {
    const res = await fetch(`/static/i18n/${lang}.json`);
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    _strings = await res.json();
  } catch {
    // Fallback to English when translation file is unavailable
    if (lang !== 'en') {
      try {
        const res = await fetch('/static/i18n/en.json');
        _strings = await res.json();
      } catch {
        _strings = {};
      }
    }
  }
}
