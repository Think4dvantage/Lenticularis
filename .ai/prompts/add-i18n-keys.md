# Prompt: Add Translated Strings

When adding any user-visible text, update all four locale files simultaneously.

## Steps

1. Add the key + English text to `static/i18n/en.json`
2. Add the translated string to `static/i18n/de.json`
3. Add the translated string to `static/i18n/fr.json`
4. Add the translated string to `static/i18n/it.json`

Use the same nested key structure as existing keys (e.g. `"admin.users.col_trusted"`, `"nav.help"`).

## In HTML (static text)

```html
<span data-i18n="your.key">Fallback text</span>
<input data-i18n-placeholder="your.placeholder_key">
```

## In JavaScript (dynamic text)

```javascript
// In module scripts (after await initI18n()):
el.textContent = window.t('your.key');
el.textContent = window.t('your.key.with_var', { varName: value });

// In non-module scripts (before initI18n() may complete):
const t = typeof window.t === 'function' ? window.t : k => k;
el.textContent = t('your.key');
```

## Key Naming Convention

- `nav.*` — navigation links
- `admin.*` — admin panel
- `editor.*` — ruleset editor
- `map.*` — map page
- `stats.*` — statistics page
- `foehn.*` — Föhn monitor
- `common.*` — shared across pages
- `auth.*` — login/register pages
- `org.*` — org dashboard / org mode
