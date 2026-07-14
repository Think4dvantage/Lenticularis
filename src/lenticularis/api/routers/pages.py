"""
Static page routes — serves the frontend HTML files.

Extracted from main.py so that main.py is not cluttered with one-liner page routes.

Asset cache-busting: every local "/static/..." reference in a page is rewritten at
serve time to append "?v=<app-version>". Combined with the immutable Cache-Control set
on versioned /static assets (see api/main.py), browsers cache assets for a year while a
version bump on deploy busts them atomically. The HTML document itself is served
`no-cache` (always revalidated via ETag) so new asset URLs are picked up immediately
after a deploy. HTML is re-read on every request so dev volume-mount edits stay live.
"""
from __future__ import annotations

import logging
import re
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import HTMLResponse, Response

logger = logging.getLogger(__name__)

router = APIRouter(include_in_schema=False)

try:
    _APP_VERSION = _pkg_version("lenticularis")
except PackageNotFoundError:
    _APP_VERSION = "0.0.0+dev"

# Resolve path: this file is at src/lenticularis/api/routers/pages.py
# parents[0] = routers/, [1] = api/, [2] = lenticularis/, [3] = src/, [4] = repo root
_STATIC = Path(__file__).resolve().parents[4] / "static"

_MAIN_SUBDOMAINS = {"www", "lenti", "lenti-dev", "localhost", ""}

# Match href="/static/..." / src="/static/..." that do not already carry a query string.
_ASSET_REF_RE = re.compile(r'(\b(?:href|src)=")(/static/[^"?]+)(")')


def _versioned_html(name: str) -> str:
    raw = (_STATIC / name).read_text(encoding="utf-8")
    return _ASSET_REF_RE.sub(
        lambda m: f"{m.group(1)}{m.group(2)}?v={_APP_VERSION}{m.group(3)}", raw
    )


def _page(name: str, request: Request) -> Response:
    """Serve an HTML page with cache-busted /static asset URLs and ETag revalidation."""
    path = _STATIC / name
    etag = f'"{_APP_VERSION}-{int(path.stat().st_mtime)}"'
    headers = {"ETag": etag, "Cache-Control": "no-cache"}
    if request.headers.get("if-none-match") == etag:
        logger.debug("Page %s not modified (etag %s) → 304", name, etag)
        return Response(status_code=304, headers=headers)
    logger.debug("Serving page %s (etag %s, version %s)", name, etag, _APP_VERSION)
    return HTMLResponse(_versioned_html(name), headers=headers)


@router.get("/")
async def serve_root(request: Request):
    host = request.headers.get("host", "").split(":")[0]
    subdomain = host.split(".")[0] if "." in host else ""
    return _page("org-dashboard.html" if subdomain not in _MAIN_SUBDOMAINS else "index.html", request)


@router.get("/org/{slug}")
async def serve_org_dashboard(slug: str, request: Request):
    return _page("org-dashboard.html", request)


@router.get("/map")
async def serve_map(request: Request):
    return _page("index.html", request)


@router.get("/stations")
async def serve_stations(request: Request):
    return _page("stations.html", request)


@router.get("/stations.html")
async def serve_stations_html(request: Request):
    return _page("stations.html", request)


@router.get("/station-detail")
async def serve_station_detail(request: Request):
    return _page("station-detail.html", request)


@router.get("/station-detail.html")
async def serve_station_detail_html(request: Request):
    return _page("station-detail.html", request)


@router.get("/login")
async def serve_login(request: Request):
    return _page("login.html", request)


@router.get("/oauth-callback")
async def serve_oauth_callback(request: Request):
    return _page("oauth-callback.html", request)


@router.get("/register")
async def serve_register(request: Request):
    return _page("register.html", request)


@router.get("/rulesets")
async def serve_rulesets(request: Request):
    return _page("rulesets.html", request)


@router.get("/ruleset-editor")
async def serve_ruleset_editor(request: Request):
    return _page("ruleset-editor.html", request)


@router.get("/ruleset-analysis")
async def serve_ruleset_analysis(request: Request):
    return _page("ruleset-analysis.html", request)


@router.get("/stats")
async def serve_stats(request: Request):
    return _page("stats.html", request)


@router.get("/foehn")
async def serve_foehn(request: Request):
    return _page("foehn.html", request)


@router.get("/wind-forecast")
async def serve_wind_forecast(request: Request):
    return _page("wind-forecast.html", request)


@router.get("/admin")
async def serve_admin(request: Request):
    return _page("admin.html", request)


@router.get("/forecast-accuracy")
async def serve_forecast_accuracy(request: Request):
    return _page("forecast-accuracy.html", request)


@router.get("/forecast-analysis")
async def serve_forecast_analysis(request: Request):
    return _page("forecast-analysis.html", request)


@router.get("/help")
async def serve_help(request: Request):
    return _page("help.html", request)
