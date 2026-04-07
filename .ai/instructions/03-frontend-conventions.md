# Frontend Conventions

## No Build Step

Changes to `static/` are live immediately in dev (volume-mounted). **Never introduce npm, webpack, vite, or any bundler.** The frontend is intentionally dependency-free.

---

## Internationalisation (i18n)

Every user-visible string must have a key in all **four** locale files simultaneously:
`static/i18n/en.json`, `de.json`, `fr.json`, `it.json`

English (`en.json`) is the source of truth. Use the same nested key structure as existing keys (e.g. `"admin.users.col_trusted"`).

### HTML

```html
<!-- Static text -->
<span data-i18n="nav.map">Map</span>

<!-- Input placeholder -->
<input data-i18n-placeholder="auth.email_placeholder">

<!-- Nav lang picker mount point (required on every page) -->
<div id="navLangPicker"></div>
```

### JavaScript

```javascript
// In module scripts (type="module") after await initI18n():
el.textContent = window.t('map.popup.wind');
el.textContent = window.t('common.elevation_asl', { m: station.elevation });

// In non-module scripts that may run before initI18n() completes:
const t = typeof window.t === 'function' ? window.t : k => k;
btn.textContent = t('map.toggle_personal_on');

// Lazy config objects (evaluated post-init, not at module load time):
function getFieldLabel() {
  return { wind_speed: window.t('editor.fields.wind_speed'), ... };
}
```

### i18n Engine (`static/i18n.js`)

- `initI18n()` — async; fetches locale JSON, calls `applyDataI18n()`, calls `renderLangPicker()`
- `window.t(key, vars?)` — returns translated string; interpolates `{placeholder}` vars; falls back to key if missing
- `applyDataI18n()` — sets `el.textContent` / `el.placeholder` for every `[data-i18n]` / `[data-i18n-placeholder]` element
- `renderLangPicker()` — injects a `<select>` into `#navLangPicker`; switching calls `setLanguage()` which reloads

Auto-detection from `navigator.language`; persists choice to `localStorage`.

---

## Module Scripts

Each page has exactly **one** `<script type="module">` block that imports from `i18n.js` and `auth.js`. Non-module scripts (e.g. `map.js`) run before `initI18n()` resolves — guard with the timing pattern above.

---

## Dark Theme

All pages share the same design system — match existing pages exactly:

| Token | Value |
|---|---|
| Body background | `#0f1117` |
| Cards / nav | `#1a1f2e` |
| Borders | `#2d3748` |
| Primary text | `#e2e8f0` |
| Accent | `#90cdf4` |

---

## Authentication

Use `fetchAuth()` from `auth.js` for all authenticated API calls. It auto-refreshes the JWT and redirects to `/login` on session expiry.

---

## Org Mode

When `orgSlug` is set:
- `applyOrgNav(slug, personalPath)` hides regular nav links, shows slug as brand, adds a "Personal workspace →" link that strips the org subdomain from `window.location.hostname`.
- Landing picker fetches from `/api/org/{slug}/rulesets` instead of `/api/rulesets`.
- Opportunity button and Public/Private toggle are hidden in the editor.

---

## Help Tips

Use `.help-tip` CSS class (defined in `shared.css`) for inline `?` tooltip buttons. All `?` links open `/help#<anchor>` in a new tab.

---

## Page Layout Pattern

Each page: one `.html` file + inline `<script type="module">` or companion `.js` file (for large pages). One HTML + script per domain — no shared mega-script.
