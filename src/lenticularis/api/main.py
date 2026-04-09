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
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

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

    # Patch scheduler to update station registry after each collect run
    _patch_scheduler_registry(scheduler, app.state.station_registry, app.state.display_registry, app.state.virtual_members, dedup_distance_m)

    # Patch scheduler to re-warm replay cache after each forecast run
    _patch_scheduler_forecast(scheduler, influx, app.state.display_registry)

    await scheduler.start()
    logger.info("Startup complete — API is ready")

    # Warm the replay cache in the background so the first user sees instant day-button responses.
    asyncio.get_event_loop().create_task(warm_replay_cache(influx, app.state.display_registry))

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


def _patch_scheduler_registry(
    scheduler: CollectorScheduler,
    registry: dict,
    display_registry: dict,
    virtual_members: dict,
    dedup_distance_m: float = 50.0,
) -> None:
    """
    Monkey-patch the scheduler's _run_collector so it also updates the
    shared station registry and rebuilds the deduped display registry
    whenever collectors return new station data.
    """
    original = scheduler._run_collector

    async def patched(collector):
        await original(collector)
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
            pass  # Registry update is best-effort

    scheduler._run_collector = patched


def _patch_scheduler_forecast(
    scheduler: CollectorScheduler,
    influx,
    display_registry: dict,
) -> None:
    """
    Monkey-patch the scheduler's _run_forecast_collector so that after each
    successful forecast run (i.e. actual points written), the stale forecast
    replay cache entries are invalidated and the warm-up task is re-fired.

    Without this, cached replay windows that include forecast data would
    continue serving the previous model run until the 5-minute TTL naturally
    expires — even though fresh data is already in InfluxDB.
    """
    logger = logging.getLogger(__name__)
    original = scheduler._run_forecast_collector

    async def patched(collector, horizon_hours):
        await original(collector, horizon_hours)
        health_key = f"forecast_{collector.SOURCE}"
        health = scheduler._collector_health.get(health_key, {})
        if health.get("status") == "ok" and (health.get("last_measurement_count") or 0) > 0:
            n = invalidate_forecast_replay_cache()
            logger.info(
                "Forecast collector '%s' wrote %d points — invalidated %d replay cache entries, re-warming",
                collector.SOURCE,
                health.get("last_measurement_count"),
                n,
            )
            asyncio.get_event_loop().create_task(warm_replay_cache(influx, display_registry))

    scheduler._run_forecast_collector = patched


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
    app.include_router(ai_router.router)
    app.include_router(org_router.router)

    # Static files (frontend)
    static_dir = Path(__file__).parent.parent.parent.parent / "static"
    if static_dir.exists():
        app.mount("/static", StaticFiles(directory=str(static_dir)), name="static")

        # Subdomains that belong to the main app — everything else is an org slug
        _MAIN_SUBDOMAINS = {"www", "lenti", "lenti-dev", "localhost", ""}

        @app.get("/", include_in_schema=False)
        async def serve_root(request: Request):
            host = request.headers.get("host", "").split(":")[0]
            subdomain = host.split(".")[0] if "." in host else ""
            if subdomain not in _MAIN_SUBDOMAINS:
                return FileResponse(str(static_dir / "org-dashboard.html"))
            return FileResponse(str(static_dir / "index.html"))

        @app.get("/org/{slug}", include_in_schema=False)
        async def serve_org_dashboard(slug: str):
            return FileResponse(str(static_dir / "org-dashboard.html"))

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

        @app.get("/oauth-callback", include_in_schema=False)
        async def serve_oauth_callback():
            return FileResponse(str(static_dir / "oauth-callback.html"))

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

        @app.get("/help", include_in_schema=False)
        async def serve_help():
            return FileResponse(str(static_dir / "help.html"))
    else:
        @app.get("/", include_in_schema=False)
        async def root():
            return {"status": "ok", "version": "0.1.0"}

    return app


app = create_app()
