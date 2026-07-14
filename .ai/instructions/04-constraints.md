# Constraints — What NOT to Do

## AI Files

**All AI-related content lives exclusively in `.ai/`.** Never create tool-specific instruction files such as `CLAUDE.md`, `.cursorrules`, `.github/copilot-instructions.md`, `.windsurfrules`, or any equivalent — not even as thin pointers. Instructions, context, prompts, and plans all go in `.ai/` and nowhere else.

---

## Production

**Never touch prod directly.** All production changes go through the IaC repo. No direct SSH, no direct `docker-compose` on the prod host.

---

## Frontend

**Never add npm or a build step.** The frontend is intentionally dependency-free. No webpack, vite, rollup, parcel, or any bundler. No `package.json`.

**Never load a library from a CDN.** Leaflet and Chart.js are self-hosted in `static/vendor/`.
The CSP in `api/main.py` is `script-src 'self'` / `style-src 'self'`, so any `unpkg.com` or
`cdn.jsdelivr.net` reference is *blocked by the browser*, not just frowned upon. New libraries
get downloaded into `static/vendor/<lib>/` and referenced by absolute `/static/…` path.

**Bump the version in `pyproject.toml` whenever static assets change.** `pages.py` cache-busts
assets with `?v=<app-version>` and `main.py` serves them `immutable, max-age=1y` — the version
*is* the cache key. Changing an asset without bumping it pins the stale file in browsers for a year.

---

## Secrets

**Never commit secrets.** `config.yml` and `.env` are gitignored. Only `config.yml.example` (with placeholder values) is committed.

---

## Database Migrations

**No Alembic. No `.sql` migration files. No `_migrations` table.**

All schema changes use raw `ALTER TABLE` statements guarded by `PRAGMA table_info()` inside `_run_column_migrations()` in `database/db.py`. `Base.metadata.create_all()` handles the initial schema at startup — it is idempotent. See `02-backend-conventions.md` for the exact pattern.

---

## i18n

**Never hardcode user-visible strings in JS** without a corresponding key in all locale files. All locales (`en.json`, `de.json`, `fr.json`, `it.json`) must be updated simultaneously.

---

## Code Quality

- Don't add features, refactor code, or make "improvements" beyond what was asked.
- Don't add error handling, fallbacks, or validation for scenarios that can't happen.
- Don't create helpers or abstractions for one-time operations.
- Don't design for hypothetical future requirements.
- Don't add docstrings, comments, or type annotations to code you didn't change.
- Don't use feature flags or backwards-compatibility shims when you can just change the code.

---

## Architecture

- No Alembic — schema migrations are done with raw `ALTER TABLE` in `_run_column_migrations()`.
- No print statements in production code — use the standard `logging` module.
- Never read `os.environ` directly — always go through `get_config()`.
- Never put page routes in `main.py` — they go in `api/routers/pages.py`.
- Never monkey-patch `scheduler` attributes — use `scheduler.on_forecast_run` / `scheduler.on_collector_run` hooks set in the lifespan.

---

## Security — Fixed Bugs, Must Not Recur

These were real vulnerabilities found and fixed in the security remediation batch. Each has a root cause that is easy to accidentally reintroduce.

### Flux injection (T01)

**Never interpolate user-supplied IDs directly into a Flux query string.**

```python
# WRONG — SQL/Flux injection
query = f'|> filter(fn: (r) => r.station_id == "{station_id}")'

# RIGHT — validate first with allowlist, then interpolate a known-safe value
import re
if not re.match(r'^[\w\-]{1,64}$', station_id):
    raise HTTPException(status_code=404)
query = f'|> filter(fn: (r) => r.station_id == "{station_id}")'
```

Validation must happen at the router level before the ID reaches `influx.py`. Station IDs and ruleset IDs (UUIDs) both need guards.

### JWT fail-closed (T02)

**The app must refuse to start if `auth.jwt_secret` is empty, too short, or a known placeholder.**

This check lives in `api/main.py` at startup. Never remove it, never bypass it for convenience, never set `jwt_secret` to a short or well-known value in any deployed environment.

### XSS — innerHTML with untrusted data (T03)

**Never assign untrusted data to `element.innerHTML`, `element.outerHTML`, or `document.write()`.**

Use `element.textContent` for plain text. If markup must be rendered (e.g. webcam links), use `sanitizeHTML(str)` (defined in the page's script) to strip everything except a known-safe allowlist of tags and attributes.

```javascript
// WRONG
el.innerHTML = station.name;      // XSS if name contains <script>

// RIGHT
el.textContent = station.name;

// RIGHT for controlled markup
el.innerHTML = sanitizeHTML(htmlFromServer);
```

### Webcam URL scheme validation (T03)

**Validate webcam URLs server-side in the Pydantic model — not just in the frontend.**

The `RulesetWebcam` model must reject any URL whose scheme is not `http` or `https`. A missing or `javascript:` scheme is invalid and must raise a `ValueError`.

### OAuth tokens in URL (T05)

**Never put `access_token` or `refresh_token` in a URL query param, hash fragment, or redirect URL.**

The OAuth callback page receives a `code` and exchanges it for tokens via a POST to `/api/auth/oauth/callback`. Tokens are stored in `localStorage` only — never embedded in a URL the browser can log.

### OAuth `email_verified` (T05)

**Always check `provider_data.get("email_verified")` before trusting an OAuth identity.**

An unverified email means the provider could not confirm the user owns that address. Treat unverified as an error — return 400, do not create or log in the user.

---

## Performance — Fixed Bugs, Must Not Recur

### Blocking the async event loop (T07, T08)

**Never call synchronous blocking I/O inside an `async def` function without `asyncio.to_thread()`.**

InfluxDB client methods (`write_points`, `query`) are synchronous. Calling them directly in an async handler blocks the entire event loop.

```python
# WRONG — blocks the event loop
async def get_latest(station_id: str, ...):
    data = influx.query_latest(station_id)   # synchronous → stalls all other requests

# RIGHT
async def get_latest(station_id: str, ...):
    data = await asyncio.to_thread(influx.query_latest, station_id)
```

This applies to the scheduler too — write calls in `_run_*_collector` must also be wrapped.

### Per-station Influx loop (T09)

**Never loop over stations to fetch Influx data one at a time when a batch method exists.**

The rules evaluator used to call `query_latest(station_id)` for every station in every ruleset. It now calls `query_latest_for_stations(station_ids)` once per evaluation. Any new code that needs latest measurements for multiple stations must use the batch path.

### Unbounded in-memory caches (T10)

**Every module-level cache dict must have a maximum size and a `threading.Lock` guard.**

A cache that grows without bound will eventually OOM the process. Pattern:

```python
import threading
_CACHE: dict[str, tuple[Any, float]] = {}
_CACHE_LOCK = threading.Lock()
_CACHE_MAX = 512

def _cache_set(key, value):
    with _CACHE_LOCK:
        if len(_CACHE) >= _CACHE_MAX:
            # evict oldest entry
            oldest = min(_CACHE, key=lambda k: _CACHE[k][1])
            del _CACHE[oldest]
        _CACHE[key] = (value, time.monotonic())
```

---

## Error Handling — Fixed Bugs, Must Not Recur

### Swallowed exceptions (T18)

**Never silence exceptions in background tasks or async callbacks.**

```python
# WRONG — hides the real error
try:
    await do_thing()
except Exception:
    pass

# WRONG — logs but continues as if nothing happened
try:
    await do_thing()
except Exception as e:
    logger.warning("thing failed: %s", e)

# RIGHT — log with full traceback and re-raise (or let it propagate)
try:
    await do_thing()
except Exception:
    logger.exception("thing failed")
    raise
```

Background tasks that swallow exceptions silently stop doing their job with no visible signal.

### RFC 7807 error envelope (T12)

**Never return a raw `HTTPException(detail="plain string")`.** Use `api_error()` from `api/errors.py`:

```python
from lenticularis.api.errors import api_error

raise api_error(404, "not_found", "Station not found", f"No station with id '{station_id}'")
```

The global exception handler in `main.py` catches unhandled exceptions and wraps them in the same envelope. Raw string `detail` fields break the contract the frontend depends on.

---

## InfluxDB Write Integrity (T19)

**Never write two fields with the same key in a single InfluxDB point.** Flux silently drops one.

**Dedup guards on `_source` tag must compare values, not just check presence.** A guard that reads `if existing._source` will always be truthy even if `existing._source != new._source` — this was a no-op that let duplicate writes through.
