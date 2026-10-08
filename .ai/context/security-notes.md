# Security Notes — Project-Specific

> Companion to `instructions/04-constraints.md`. That file holds the generic, blueprint-owned
> hard rules; this file holds the fixed-bug history behind them — real vulnerabilities and
> incidents found and fixed in this project, each with a root cause that is easy to accidentally
> reintroduce. Update this file freely; it is never touched by `update-blueprint.md`.

---

## Security — Fixed Bugs, Must Not Recur

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

### Typed error envelope (T12)

**Every error response must leave the app as `{"error": {"code", "message", "details"}}`.** Three
handlers in `api/main.py` guarantee this — nothing else may return a bare `JSONResponse` for an error.

To attach a *specific* error code, raise `AppException` from `api/errors.py`:

```python
from lenticularis.api.errors import AppException

raise AppException(404, "ENTITY_NOT_FOUND", "Station not found", {"station_id": station_id})
```

`code` is the UPPERCASE vocabulary from `07-api-conventions.md`; `details` is a **dict**, not a string.

Raising a plain `HTTPException` is also safe — the `HTTPException` handler in `main.py` maps the
status onto a code via `_STATUS_TO_CODE` (400→`VALIDATION_FAILED`, 401→`AUTH_REQUIRED`,
403→`PERMISSION_DENIED`, 404→`ENTITY_NOT_FOUND`, 409→`CONFLICT`, other 5xx→`INTERNAL_ERROR`,
else `ERROR`) and wraps it in the same envelope. **This is what every router currently does.**

Use `AppException` when the status code alone does not identify the failure (two different 409s,
a 400 that is not a validation error) or when the frontend needs `details`. Otherwise `HTTPException`
is fine — the envelope holds either way.

> Not RFC 7807. RFC 7807 is `type`/`title`/`status`/`detail`/`instance` under
> `application/problem+json`. This envelope is the project's own shape — do not rename it back.

---

## InfluxDB Write Integrity (T19)

**Never write two fields with the same key in a single InfluxDB point.** Flux silently drops one.

**Dedup guards on `_source` tag must compare values, not just check presence.** A guard that reads `if existing._source` will always be truthy even if `existing._source != new._source` — this was a no-op that let duplicate writes through.

---

## Public MCP server — exposure rules (v1.24.0)

### Private-station leak through dedup pooling (found in design, closed by construction)

**Never serve MCP data through the website's `display_registry` / `virtual_members`.** Website dedup
merges co-located stations across every network, and `query_latest_virtual` / `query_history_virtual`
pool all members newest-wins — so filtering a response's final station list is not enough: a MeteoSwiss
station with an Ecowitt neighbour would return the Ecowitt reading. `McpRegistry` removes unverified
networks **before** dedup. Regression tests: `tests/backend/test_mcp_registry.py`
(`test_dedup_member_leak_closed`, `test_private_canonical_cluster_does_not_hide_verified_member`).

### Allowlist, fail-closed

`mcp.verified_networks` is an allowlist. Wunderground and Ecowitt are operated by private individuals
and are deliberately excluded. New collectors are hidden until added (`prompts/add-collector.md` 3b).
**Assumption recorded 2026-10-08 (owner):** Holfuy data may be redistributed (winds.mobi redistributes
it); if that changes, remove `holfuy` from `verified_networks` — config only.

### Known-faulty values

`mcp_server/sanitize.py:SUPPRESSED_FIELDS` — `jfb-hollandiahutte-sac` declares 3248 m but reports a
~750 m reading (~928 hPa / 23 °C): temperature, humidity and both pressures are never emitted. For a
merged group a field is dropped if ANY member suppresses it (pooled values cannot be attributed).

### Föhn inputs are admin-editable at runtime

`PUT /api/foehn/config?set_as_default=true` can change the system default config while the app runs, so
`get_foehn_status` drops any input station whose network is not verified on **every call** (not just at
startup). A test also asserts today's default config only references verified networks.

### Abuse control and error hygiene

Anonymous endpoint ⇒ per-caller rate limit (`mcp.rate_limit_per_minute`, bounded table), keyed by the
rightmost `X-Forwarded-For` entry (the address our own proxy saw; left entries are client-forgeable) and
hashed with a random per-process salt (an unsalted IPv4 hash is reversible). Unexpected exceptions are
logged with `logger.exception` and returned as a generic `INTERNAL_ERROR` — the SDK's default error text
could otherwise leak Flux queries or hostnames. A Traefik `rateLimit` middleware (lg4 IaC repo) is the
recommended complement.
