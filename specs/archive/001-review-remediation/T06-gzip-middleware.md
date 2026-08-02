# T06 — Add GZip compression middleware

**Severity:** Medium (high value/effort ratio) · **Phase:** 2 · **Model tier:** Trivial

## Ground Rules
- LF line endings only. No new dependencies (`GZipMiddleware` ships with Starlette). Exactly this task.

## Problem
No compression middleware exists. The replay endpoint (all stations × frames) and the wind-forecast
grid (up to 1272 points × ~30 frames × 3 arrays) ship as uncompressed JSON. This data compresses
~80–90%.

## Fix
In `src/lenticularis/api/main.py`, inside `create_app()` right after `app = FastAPI(...)`:
```python
from fastapi.middleware.gzip import GZipMiddleware
app.add_middleware(GZipMiddleware, minimum_size=1000)
```
If T04 also adds middleware, ordering does not matter functionally here; add this line alongside it.

## Acceptance criteria
- `curl -s -H 'Accept-Encoding: gzip' -D - '/api/stations/replay?hours=24' -o /dev/null` shows
  `content-encoding: gzip` for the large response.
- A small response (< 1000 bytes) is not gzipped.
- Pages still load and parse the responses normally.
