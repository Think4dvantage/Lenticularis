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
from lenticularis.scheduler import CollectorScheduler
from lenticularis.collectors.meteoswiss import MeteoSwissCollector
from lenticularis.api.routers import stations as stations_router


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
        if collector_cfg.name == "meteoswiss":
            tmp = MeteoSwissCollector(config=collector_cfg.config)
            try:
                stations = await tmp.get_stations()
                for s in stations:
                    app.state.station_registry[s.station_id] = s
                logger.info("Station registry primed with %d MeteoSwiss stations", len(stations))
            except Exception as exc:
                logger.warning("Could not prime station registry: %s", exc)
            finally:
                await tmp.close()

    # Scheduler
    scheduler = CollectorScheduler(cfg, influx)
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

    # Static files (frontend)
    static_dir = Path(__file__).parent.parent.parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        @app.get("/", include_in_schema=False)
        async def serve_index():
            return FileResponse(str(static_dir / "index.html"))
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()
