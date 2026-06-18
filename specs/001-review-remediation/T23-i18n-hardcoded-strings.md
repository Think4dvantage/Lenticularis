# T23 — Replace remaining hardcoded UI strings with i18n keys

**Severity:** Low · **Phase:** 4 · **Model tier:** Trivial

## Ground Rules
- Read `.ai/instructions/03-frontend-conventions.md` (i18n) and `04-constraints.md` ("Never hardcode
  user-visible strings without a key in all locale files"). LF line endings only.
- English (`en.json`) is the source of truth; add the SAME key to **all four** locale files
  simultaneously: `static/i18n/{en,de,fr,it}.json`. Keep the existing nested key structure.

## Problem
Newer pages (e.g. `forecast-analysis`) are fully i18n-keyed, but older pages still hardcode English:
- `static/ruleset-editor.html` ~lines 899–901: `Active` / `Inactive` `<option>` labels.
- `static/foehn.html` ~lines 639–640, 710–711: North-Föhn risk title + "Pressure building/ok".
- `'No matches'` station-search empty state, duplicated in `static/ruleset-editor.html` (~844) and
  `static/ruleset-analysis.html` (~843).
(Grep each file for obvious English literals in rendered markup/JS to catch any nearby siblings.)

## Fix
1. For each hardcoded string, add a key under a sensible existing namespace (e.g.
   `ruleset_editor.active`, `ruleset_editor.inactive`, `foehn.north_risk_title`,
   `foehn.pressure_building`, `common.no_matches`) to **all four** locale files. Provide real DE/FR/IT
   translations (the project already has native-quality translations elsewhere — match their tone; if
   unsure of a term, reuse phrasing from an equivalent existing key).
2. Replace the literals:
   - Static HTML text → `data-i18n="..."` (or `data-i18n` on the element) per the conventions.
   - JS-built strings → `window.t('common.no_matches')` (guard with the
     `const t = typeof window.t === 'function' ? window.t : k => k;` pattern in non-module scripts).
3. Reuse one shared key (`common.no_matches`) for both duplicate "No matches" sites.

## Acceptance criteria
- The listed strings render translated when the UI language is DE/FR/IT (no English leaks).
- All four locale JSONs contain the new keys with the same structure and parse as valid JSON
  (line-count stays aligned, matching the existing convention).
- No remaining hardcoded English in the touched blocks (grep the changed regions).
