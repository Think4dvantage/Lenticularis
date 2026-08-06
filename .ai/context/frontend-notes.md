# Frontend Notes — Project-Specific

> Companion to `instructions/03-frontend-conventions.md`. That file holds the generic,
> blueprint-owned patterns; this file holds Lenticularis's own asset pipeline, nav
> architecture, and logging namespace. Update this file freely; it is never touched by
> `update-blueprint.md`.

---

## No CDN — Third-Party Libraries Are Self-Hosted

**Never add a `<script src="https://…">` or `<link href="https://…">` to any page.** Leaflet
and Chart.js live in `static/vendor/` and are loaded from there:

```html
<link rel="stylesheet" href="/static/vendor/leaflet/leaflet.css" />
<script src="/static/vendor/leaflet/leaflet.js"></script>
<script src="/static/vendor/chartjs/chart.umd.min.js"></script>
```

The Content-Security-Policy in `api/main.py` sets `script-src 'self'` / `style-src 'self'`,
so a CDN reference will be **blocked by the browser**, not merely discouraged.

To add a new library: download the dist file into `static/vendor/<lib>/`, reference it with an
absolute `/static/…` path, and nothing else. `.gitattributes` marks `static/vendor/** -text`
so vendored files stay byte-exact.

> Note: Leaflet's `marker-icon.png` / `layers.png` are intentionally **not** vendored. Every
> marker in the app is an `L.divIcon` or `L.circleMarker` and no `L.control.layers` is used,
> so `leaflet.css` never requests them. If you ever add a default marker or a layers control,
> you must vendor `static/vendor/leaflet/images/` too.

---

## Static Asset Caching & Cache-Busting

Handled entirely server-side — there is nothing to do in a page, but do not fight it:

- `api/routers/pages.py` rewrites every local `href="/static/…"` / `src="/static/…"` in a page
  at serve time to append `?v=<app-version>`.
- `api/main.py` serves versioned `/static` URLs as `immutable, max-age=1y`; unversioned hits
  (locale JSON, ES-module imports) get `max-age=600`.
- HTML is served `no-cache` with an ETag and revalidates to `304`.

**A deploy therefore requires a version bump in `pyproject.toml`** — the version *is* the cache
key. Shipping changed assets without bumping it leaves stale files pinned in browsers for a year.

---

## Nav / Bootstrap

`static/shared.css` owns **all** nav CSS (`.top-nav`, `.nav-brand`, `.nav-links`, `.nav-link`, `.nav-user`, `.nav-btn`, `.lang-btn`, `#navLangPicker`). **Never duplicate nav CSS in a page `<style>` block.**

`static/bootstrap.js` provides two exports:

| Export | Purpose |
|---|---|
| `renderNav(mount)` | Inject the standard 8-link `<nav>` into a mount element; marks current-page link `.active` by `pathname` match |
| `bootstrapPage(opts)` | Full page bootstrap: `renderNav` (if `#appNav` found) → `initI18n` → `renderNavAuth` → `renderLangPicker` |

### Standard pages — full migration

Pages whose `<nav>` is exactly the standard 8-link nav use a `<div id="appNav"></div>` placeholder:

```html
<!-- In <body>, replaces <nav class="top-nav"> -->
<div id="appNav"></div>
```

```javascript
// module script — replaces manual initI18n + renderNavAuth + renderLangPicker calls
import { bootstrapPage } from '/static/bootstrap.js';
import { fetchAuth } from '/static/auth.js';   // keep other auth imports as needed
await bootstrapPage({ page: 'pagename' });
```

### Non-standard pages — CSS-only migration

Pages with page-specific nav content (extra status dot, extra nav links, auth-guarded redirect between init and auth render) **keep their own inline `<nav>`** — no `#appNav` div. `bootstrapPage` safely skips nav injection when `#appNav` is not found, but still runs initI18n/renderNavAuth/renderLangPicker.

Pages and their variation:

| Page | Reason for inline nav |
|---|---|
| `stations.html` | `nav-status` live-data dot + timestamp inside nav |
| `index.html` | `nav-status` dot + `dispatchEvent('i18nReady')` between initI18n and renderNavAuth |
| `admin.html` | Extra `/admin` nav link + `dispatchEvent('i18nReady')` |
| `login.html` | Condensed 3-link nav |

For these pages, remove any inline nav CSS block (now covered by `shared.css`) but leave the `<nav>` markup and the bootstrap script unchanged.

---

## XSS — Frontend Specifics

Station names, ruleset names, user content from the API — all of it is untrusted. Use
`element.textContent` for text. If markup must be rendered, pipe it through `sanitizeHTML()`
(strip all tags except a known-safe allowlist defined per-page). See `context/security-notes.md`
(T03) for the fixed-bug history behind this rule.

```javascript
// WRONG
el.innerHTML = apiResponse.name;

// RIGHT
el.textContent = apiResponse.name;
```

---

## Console Logging Namespace

The prefix namespace is `Lenti`:

```
[Lenti:map]      map page
[Lenti:auth]     auth / login page
[Lenti:admin]    admin panel
[Lenti:<page>]   derive from the HTML filename
```

Used by every page except `static/forecast-analysis.js`, which still emits `[App:…]` and is the
odd one out. Match `Lenti` in new code.
