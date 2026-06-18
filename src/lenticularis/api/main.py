"""
FastAPI application factory and lifespan for Lenticularis.

Startup sequence:
  1. Load config from config.yml
  2. Configure logging
  3. Initialise InfluxDB client and store on app.state
  4. Start the collector scheduler (triggers first run with jitter)
  5. Warm up station registry from all enabled collectors

Shutdown:
  1. Stop scheduler and close collector HTTP clients
  2. Close InfluxDB client
"""

from __future__ import annotations

import asyncio
import logging
import logging.config
from contextlib import asynccontextmanager
from importlib.metadata import PackageNotFoundError, version as _pkg_version
from pathlib import Path

try:
    _APP_VERSION = _pkg_version("lenticularis")
except PackageNotFoundError:
    _APP_VERSION = "0.0.0+dev"

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.middleware.cors import CORSMiddleware
from fastapi.middleware.gzip import GZipMiddleware
from starlette.middleware.base import BaseHTTPMiddleware

from lenticularis.api.errors import AppException, _envelope

from lenticularis.config import get_config
from lenticularis.database.influx import InfluxClient
from lenticularis.scheduler import CollectorScheduler, get_collector_class
from lenticularis.api.routers import stations as stations_router
from lenticularis.api.routers.stations import warm_replay_cache, invalidate_forecast_replay_cache
from lenticularis.api.routers import auth as auth_router
from lenticularis.api.routers import rulesets as rulesets_router
from lenticularis.api.routers import health as health_router
from lenticularis.api.routers import foehn as foehn_router
from lenticularis.api.routers import stats as stats_router
from lenticularis.api.routers import admin as admin_router
from lenticularis.api.routers import ai as ai_router
from lenticularis.api.routers import org as org_router
from lenticularis.api.routers import wind_forecast as wind_forecast_router
from lenticularis.database.db import init_db, get_session_factory
from lenticularis.collectors.foehn import _VIRTUAL_WEATHER_STATIONS
from lenticularis.services.dedup import build_deduped_registry


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------

def _configure_logging(cfg) -> None:
    log_cfg = cfg.logging
    level = getattr(logging, log_cfg.level.upper(), logging.INFO)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    if log_cfg.file:
        log_dir = Path(log_cfg.file).parent
        log_dir.mkdir(parents=True, exist_ok=True)
        handlers.append(logging.FileHandler(log_cfg.file))
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-8s %(name)s  %(message)s",
        handlers=handlers,
    )


# ---------------------------------------------------------------------------
# Lifespan
# ---------------------------------------------------------------------------

_PLACEHOLDER_SECRETS = {
    "change-me-in-production",
    "change-me-in-production-use-openssl-rand-hex-32",
    "dev-secret-change-in-production",
    "",
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and graceful shutdown."""
    cfg = get_config()
    _configure_logging(cfg)
    logger = logging.getLogger(__name__)
    logger.info("Lenticularis starting — version %s", _APP_VERSION)

    _secret = cfg.auth.jwt_secret
    if _secret in _PLACEHOLDER_SECRETS or len(_secret) < 32:
        logger.critical(
            "auth.jwt_secret is unset, a known placeholder, or shorter than 32 chars — refusing to start. "
            "Generate one with: openssl rand -hex 32"
        )
        raise RuntimeError("Insecure auth.jwt_secret — see logs")

    # SQLite
    init_db(cfg.database.path)

    # InfluxDB
    influx = InfluxClient(cfg.influxdb)
    app.state.influx = influx

    # Station registry — shared mutable dict populated by collectors
    app.state.station_registry = {}

    # Prime the station registry from every enabled collector before we open
    # the HTTP server so the first /api/stations call isn't empty.
    for collector_cfg in cfg.collectors:
        if not collector_cfg.enabled:
            continue

        collector_cls = get_collector_class(collector_cfg.name)
        if collector_cls is None:
            logger.warning("Could not prime station registry for unknown collector '%s'", collector_cfg.name)
            continue

        tmp = collector_cls(config=collector_cfg.config)
        try:
            stations = await tmp.get_stations()
            for s in stations:
                app.state.station_registry[s.station_id] = s
            logger.info("Station registry primed with %d %s stations", len(stations), collector_cfg.name)
        except Exception as exc:
            logger.warning("Could not prime station registry for '%s': %s", collector_cfg.name, exc)
        finally:
            await tmp.close()

    # Pre-seed föhn virtual stations so they appear on the map immediately at startup
    # (the FoehnCollector also adds them after its first run, but that has jitter delay)
    for ws in _VIRTUAL_WEATHER_STATIONS:
        app.state.station_registry[ws.station_id] = ws
    logger.info("Station registry primed with %d foehn virtual stations", len(_VIRTUAL_WEATHER_STATIONS))

    # Build the deduplicated display registry (merges co-located stations)
    app.state.display_registry = {}
    app.state.virtual_members = {}
    dedup_distance_m = cfg.station_dedup.distance_m
    app.state.dedup_distance_m = dedup_distance_m

    from lenticularis.database.models import StationDedupOverride
    from sqlalchemy import select as _select
    _db = get_session_factory()()
    try:
        _overrides = _db.execute(_select(StationDedupOverride)).scalars().all()
        manual_pairs = [(_o.station_id_a, _o.station_id_b) for _o in _overrides]
    finally:
        _db.close()

    display_reg, virt_members = build_deduped_registry(app.state.station_registry, dedup_distance_m, manual_pairs)
    app.state.display_registry.update(display_reg)
    app.state.virtual_members.update(virt_members)
    logger.info(
        "Display registry built: %d display stations from %d raw stations (%d virtual groups)",
        len(display_reg), len(app.state.station_registry), len(virt_members),
    )

    # Scheduler
    scheduler = CollectorScheduler(
        cfg, influx, app.state.station_registry, get_session_factory(),
        virtual_members=app.state.virtual_members,
    )
    app.state.scheduler = scheduler

    # Wire post-run hooks (replaces monkey-patching)
    scheduler.on_collector_run = _make_registry_updater(
        app.state.station_registry, app.state.display_registry, app.state.virtual_members, dedup_distance_m
    )
    scheduler.on_forecast_run = _make_forecast_rewarmer(influx, app.state.display_registry)

    await scheduler.start()
    logger.info("Startup complete — API is ready")

    # Warm the replay cache in the background so the first user sees instant day-button responses.
    asyncio.get_event_loop().create_task(warm_replay_cache(influx, app.state.display_registry))

    # Backfill forecast_deviation if the measurement is sparse (runs once, no-ops if data exists).
    asyncio.get_event_loop().create_task(scheduler.run_forecast_deviation_backfill_if_needed())

    yield  # Server is running

    # Graceful shutdown
    logger.info("Shutting down…")
    await scheduler.stop()
    influx.close()
    logger.info("Shutdown complete")


def rebuild_display_registry(app_state) -> None:
    """Reload manual dedup pairs from DB and rebuild the display registry in-place."""
    from lenticularis.database.models import StationDedupOverride
    from sqlalchemy import select as _select
    from lenticularis.database.db import get_session_factory
    db = get_session_factory()()
    try:
        overrides = db.execute(_select(StationDedupOverride)).scalars().all()
        manual_pairs = [(o.station_id_a, o.station_id_b) for o in overrides]
    finally:
        db.close()
    distance_m = getattr(app_state, "dedup_distance_m", 50.0)
    new_display, new_virtual = build_deduped_registry(app_state.station_registry, distance_m, manual_pairs)
    app_state.display_registry.clear()
    app_state.display_registry.update(new_display)
    app_state.virtual_members.clear()
    app_state.virtual_members.update(new_virtual)


def _make_registry_updater(registry: dict, display_registry: dict, virtual_members: dict, dedup_distance_m: float):
    """Return an async callback that rebuilds the station display registry after each observation run."""
    _log = logging.getLogger(__name__)

    async def _on_collector_run(collector):
        try:
            stations = await collector.get_stations()
            changed = False
            for s in stations:
                if s.station_id not in registry:
                    changed = True
                registry[s.station_id] = s
            if changed:
                from lenticularis.database.models import StationDedupOverride
                from sqlalchemy import select as _select
                from lenticularis.database.db import get_session_factory
                db = get_session_factory()()
                try:
                    overrides = db.execute(_select(StationDedupOverride)).scalars().all()
                    manual_pairs = [(o.station_id_a, o.station_id_b) for o in overrides]
                finally:
                    db.close()
                new_display, new_virtual = build_deduped_registry(registry, dedup_distance_m, manual_pairs)
                display_registry.clear()
                display_registry.update(new_display)
                virtual_members.clear()
                virtual_members.update(new_virtual)
        except Exception:
            _log.warning("Registry update after collector run failed (best-effort)", exc_info=True)

    return _on_collector_run


def _make_forecast_rewarmer(influx, display_registry: dict):
    """Return an async callback that invalidates + re-warms the forecast replay cache after each forecast run."""
    _log = logging.getLogger(__name__)

    async def _on_forecast_run(collector, horizon_hours, health):
        if health.get("status") == "ok" and (health.get("last_measurement_count") or 0) > 0:
            n = invalidate_forecast_replay_cache()
            _log.info(
                "Forecast collector '%s' wrote %d points — invalidated %d replay cache entries, re-warming",
                collector.SOURCE,
                health.get("last_measurement_count"),
                n,
            )
            asyncio.get_event_loop().create_task(warm_replay_cache(influx, display_registry))

    return _on_forecast_run


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Lenticularis",
        description="Paragliding weather decision-support system for Switzerland",
        version=_APP_VERSION,
        lifespan=lifespan,
    )

    async def _security_headers(request: Request, call_next):
        resp = await call_next(request)
        resp.headers.setdefault("X-Content-Type-Options", "nosniff")
        resp.headers.setdefault("X-Frame-Options", "DENY")
        resp.headers.setdefault("Referrer-Policy", "strict-origin-when-cross-origin")
        resp.headers.setdefault(
            "Content-Security-Policy",
            "default-src 'self'; "
            "img-src 'self' data: https:; "
            "script-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
            "style-src 'self' 'unsafe-inline' https://unpkg.com https://cdn.jsdelivr.net; "
            "connect-src 'self'; frame-ancestors 'none'",
        )
        return resp

    app.add_middleware(BaseHTTPMiddleware, dispatch=_security_headers)
    app.add_middleware(GZipMiddleware, minimum_size=1000)

    # ---------------------------------------------------------------------------
    # Exception handlers — RFC7807 envelope for all error responses
    # ---------------------------------------------------------------------------
    _STATUS_TO_CODE = {
        400: "VALIDATION_FAILED",
        401: "AUTH_REQUIRED",
        403: "PERMISSION_DENIED",
        404: "ENTITY_NOT_FOUND",
        409: "CONFLICT",
    }
    _logger = logging.getLogger(__name__)

    @app.exception_handler(AppException)
    async def _app_exc(request: Request, exc: AppException):
        if exc.status_code >= 500:
            _logger.error("%s %s → %s %s", request.method, request.url.path, exc.code, exc.message)
        return JSONResponse(status_code=exc.status_code, content=_envelope(exc.code, exc.message, exc.details))

    @app.exception_handler(HTTPException)
    async def _http_exc(request: Request, exc: HTTPException):
        code = _STATUS_TO_CODE.get(exc.status_code, "INTERNAL_ERROR" if exc.status_code >= 500 else "ERROR")
        if exc.status_code >= 500:
            _logger.error("%s %s → HTTP %d %s", request.method, request.url.path, exc.status_code, exc.detail)
        return JSONResponse(status_code=exc.status_code, content=_envelope(code, str(exc.detail)), headers=exc.headers)

    @app.exception_handler(RequestValidationError)
    async def _validation_exc(request: Request, exc: RequestValidationError):
        return JSONResponse(status_code=422, content=_envelope("VALIDATION_FAILED", "Request validation failed", {"errors": exc.errors()}))

    # Cross-origin clients (e.g. the mobile app) — add explicit origins here, never "*":
    # app.add_middleware(CORSMiddleware, allow_origins=["https://lenti.cloud"],
    #                    allow_credentials=True, allow_methods=["*"], allow_headers=["*"])

    # API routers
    app.include_router(stations_router.router)
    app.include_router(auth_router.router)
    app.include_router(rulesets_router.router)
    app.include_router(health_router.router)
    app.include_router(foehn_router.router)
    app.include_router(stats_router.router)
    app.include_router(admin_router.router)
    app.include_router(ai_router.router)
    app.include_router(org_router.router)
    app.include_router(wind_forecast_router.router)

    # Static files (frontend) + page routes
    static_dir = Path(__file__).parent.parent.parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")
        from lenticularis.api.routers import pages as pages_router
        app.include_router(pages_router.router)
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"status": "ok", "version": _APP_VERSION}

    return app


app = create_app()
