# T13 — Move inline page routes out of main.py into a pages router

**Severity:** Medium · **Phase:** 3 · **Model tier:** Moderate

## Ground Rules
- Read `.ai/instructions/02-backend-conventions.md` ("never put all routes in main.py — one router
  per domain") and `04-constraints.md`. LF line endings only. Exactly this task — pure move/refactor,
  no behavior change.

## Problem
`src/lenticularis/api/main.py` defines ~22 inline static-page routes (`serve_root`, `serve_map`,
`serve_stations`, …, `serve_help`) directly in `create_app()`, violating the router-per-domain rule.

## Fix
Create `src/lenticularis/api/routers/pages.py` exposing a router that serves the static HTML pages.
Because the routes need the `static_dir` path and the subdomain logic, encapsulate that:
```python
from __future__ import annotations
from pathlib import Path
from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)
_STATIC = Path(__file__).resolve().parents[3] / "static"   # verify this resolves to <repo>/static
_MAIN_SUBDOMAINS = {"www", "lenti", "lenti-dev", "localhost", ""}

def _page(name: str) -> FileResponse:
    return FileResponse(str(_STATIC / name))

@router.get("/")
async def serve_root(request: Request):
    host = request.headers.get("host", "").split(":")[0]
    subdomain = host.split(".")[0] if "." in host else ""
    return _page("org-dashboard.html" if subdomain not in _MAIN_SUBDOMAINS else "index.html")

@router.get("/org/{slug}")
async def serve_org_dashboard(slug: str):
    return _page("org-dashboard.html")

# ... one route per existing serve_* handler, same paths, same target files ...
@router.get("/help")
async def serve_help():
    return _page("help.html")
```
Move **all** `serve_*` handlers from `main.py` into this file verbatim (same paths, same target HTML,
same `include_in_schema=False`). Keep the `/static` mount and the `else` fallback (`root()` returning
`{"status": "ok"}` when `static_dir` is missing) where they are — only the page routes move.

In `main.py`, register the router **after** the `/static` mount and only when `static_dir` exists:
```python
if static_dir.exists():
    app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
    from lenticularis.api.routers import pages as pages_router
    app.include_router(pages_router.router)
else:
    @app.get("/", include_in_schema=False)
    async def root():
        return {"status": "ok", "version": app.version}
```
Verify `_STATIC` in `pages.py` resolves to the same directory `main.py` computes
(`Path(__file__).parent.parent.parent.parent / "static"`). Adjust `parents[n]` accordingly.

## Acceptance criteria
- `main.py` no longer contains any `serve_*` page handlers; they live in `pages.py`.
- Every page URL that worked before still returns its HTML: `/`, `/map`, `/stations`, `/station-detail`,
  `/login`, `/oauth-callback`, `/register`, `/rulesets`, `/ruleset-editor`, `/ruleset-analysis`,
  `/stats`, `/foehn`, `/wind-forecast`, `/admin`, `/forecast-accuracy`, `/forecast-analysis`, `/help`,
  `/org/{slug}`, and the subdomain root behavior.
- API routers and `/static` still work.
