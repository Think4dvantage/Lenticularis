"""
Static page routes — serves the frontend HTML files.

Extracted from main.py so that main.py is not cluttered with one-liner page routes.
"""
from __future__ import annotations

from pathlib import Path

from fastapi import APIRouter, Request
from fastapi.responses import FileResponse

router = APIRouter(include_in_schema=False)

# Resolve path: this file is at src/lenticularis/api/routers/pages.py
# parents[0] = routers/, [1] = api/, [2] = lenticularis/, [3] = src/, [4] = repo root
_STATIC = Path(__file__).resolve().parents[4] / "static"

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


@router.get("/map")
async def serve_map():
    return _page("index.html")


@router.get("/stations")
async def serve_stations():
    return _page("stations.html")


@router.get("/stations.html")
async def serve_stations_html():
    return _page("stations.html")


@router.get("/station-detail")
async def serve_station_detail():
    return _page("station-detail.html")


@router.get("/station-detail.html")
async def serve_station_detail_html():
    return _page("station-detail.html")


@router.get("/login")
async def serve_login():
    return _page("login.html")


@router.get("/oauth-callback")
async def serve_oauth_callback():
    return _page("oauth-callback.html")


@router.get("/register")
async def serve_register():
    return _page("register.html")


@router.get("/rulesets")
async def serve_rulesets():
    return _page("rulesets.html")


@router.get("/ruleset-editor")
async def serve_ruleset_editor():
    return _page("ruleset-editor.html")


@router.get("/ruleset-analysis")
async def serve_ruleset_analysis():
    return _page("ruleset-analysis.html")


@router.get("/stats")
async def serve_stats():
    return _page("stats.html")


@router.get("/foehn")
async def serve_foehn():
    return _page("foehn.html")


@router.get("/wind-forecast")
async def serve_wind_forecast():
    return _page("wind-forecast.html")


@router.get("/admin")
async def serve_admin():
    return _page("admin.html")


@router.get("/forecast-accuracy")
async def serve_forecast_accuracy():
    return _page("forecast-accuracy.html")


@router.get("/forecast-analysis")
async def serve_forecast_analysis():
    return _page("forecast-analysis.html")


@router.get("/help")
async def serve_help():
    return _page("help.html")
