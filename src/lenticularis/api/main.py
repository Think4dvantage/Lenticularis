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

import logging
import logging.config
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from lenticularis.config import get_config
from lenticularis.database.influx import InfluxClient
from lenticularis.scheduler import CollectorScheduler, get_collector_class
from lenticularis.api.routers import stations as stations_router
from lenticularis.api.routers import auth as auth_router
from lenticularis.api.routers import rulesets as rulesets_router
from lenticularis.api.routers import health as health_router
from lenticularis.api.routers import foehn as foehn_router
from lenticularis.api.routers import stats as stats_router
from lenticularis.api.routers import admin as admin_router
from lenticularis.database.db import init_db
from lenticularis.collectors.foehn import _VIRTUAL_WEATHER_STATIONS


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

@asynccontextmanager
async def lifespan(app: FastAPI):
    """Handle startup and graceful shutdown."""
    cfg = get_config()
    _configure_logging(cfg)
    logger = logging.getLogger(__name__)
    logger.info("Lenticularis starting up…")

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

    # Scheduler
    scheduler = CollectorScheduler(cfg, influx, app.state.station_registry)
    app.state.scheduler = scheduler

    # Patch scheduler to update station registry after each collect run
    _patch_scheduler_registry(scheduler, app.state.station_registry)

    await scheduler.start()
    logger.info("Startup complete — API is ready")

    yield  # Server is running

    # Graceful shutdown
    logger.info("Shutting down…")
    await scheduler.stop()
    influx.close()
    logger.info("Shutdown complete")


def _patch_scheduler_registry(scheduler: CollectorScheduler, registry: dict) -> None:
    """
    Monkey-patch the scheduler's _run_collector so it also updates the
    shared station registry whenever collectors return new station data.
    """
    original = scheduler._run_collector

    async def patched(collector):
        await original(collector)
        try:
            stations = await collector.get_stations()
            for s in stations:
                registry[s.station_id] = s
        except Exception:
            pass  # Registry update is best-effort

    scheduler._run_collector = patched


# ---------------------------------------------------------------------------
# App factory
# ---------------------------------------------------------------------------

def create_app() -> FastAPI:
    app = FastAPI(
        title="Lenticularis",
        description="Paragliding weather decision-support system for Switzerland",
        version="0.1.0",
        lifespan=lifespan,
    )

    # API routers
    app.include_router(stations_router.router)
    app.include_router(auth_router.router)
    app.include_router(rulesets_router.router)
    app.include_router(health_router.router)
    app.include_router(foehn_router.router)
    app.include_router(stats_router.router)
    app.include_router(admin_router.router)

    # Static files (frontend)
    static_dir = Path(__file__).parent.parent.parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_root():
            return FileResponse(str(static_dir / "index.html"))

        @app.get("/map", include_in_schema=False)
        async def serve_map():
            return FileResponse(str(static_dir / "index.html"))

        @app.get("/stations", include_in_schema=False)
        async def serve_stations():
            return FileResponse(str(static_dir / "stations.html"))

        @app.get("/stations.html", include_in_schema=False)
        async def serve_stations_html():
            return FileResponse(str(static_dir / "stations.html"))

        @app.get("/station-detail", include_in_schema=False)
        async def serve_station_detail():
            return FileResponse(str(static_dir / "station-detail.html"))

        @app.get("/station-detail.html", include_in_schema=False)
        async def serve_station_detail_html():
            return FileResponse(str(static_dir / "station-detail.html"))

        @app.get("/login", include_in_schema=False)
        async def serve_login():
            return FileResponse(str(static_dir / "login.html"))

        @app.get("/register", include_in_schema=False)
        async def serve_register():
            return FileResponse(str(static_dir / "register.html"))

        @app.get("/rulesets", include_in_schema=False)
        async def serve_rulesets():
            return FileResponse(str(static_dir / "rulesets.html"))

        @app.get("/ruleset-editor", include_in_schema=False)
        async def serve_ruleset_editor():
            return FileResponse(str(static_dir / "ruleset-editor.html"))

        @app.get("/ruleset-analysis", include_in_schema=False)
        async def serve_ruleset_analysis():
            return FileResponse(str(static_dir / "ruleset-analysis.html"))

        @app.get("/stats", include_in_schema=False)
        async def serve_stats():
            return FileResponse(str(static_dir / "stats.html"))

        @app.get("/foehn", include_in_schema=False)
        async def serve_foehn():
            return FileResponse(str(static_dir / "foehn.html"))

        @app.get("/admin", include_in_schema=False)
        async def serve_admin():
            return FileResponse(str(static_dir / "admin.html"))

        @app.get("/forecast-accuracy", include_in_schema=False)
        async def serve_forecast_accuracy():
            return FileResponse(str(static_dir / "forecast-accuracy.html"))
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()
