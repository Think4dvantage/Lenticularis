"""
APScheduler wiring for Lenticularis.

Schedules all enabled collectors and submits their results to InfluxDB.
A small random jitter is applied to each job's first run to prevent
every collector from firing simultaneously at startup.

Forecast collectors
-------------------
Forecast collectors (``BaseForecastCollector`` subclasses) are registered
separately via ``_FORECAST_REGISTRY``.  They require a station list with
lat/lon — provided via the shared ``station_registry`` dict that observation
collectors populate during their runs.  The scheduler passes the current
registry snapshot to each forecast collector run.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any, Optional

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from lenticularis.collectors.ecowitt import EcowittCollector
from lenticularis.collectors.foehn import FoehnCollector
from lenticularis.collectors.forecast_openmeteo import ForecastOpenMeteoCollector
from lenticularis.collectors.metar import MetarCollector
from lenticularis.collectors.meteoswiss import MeteoSwissCollector
from lenticularis.collectors.slf import SlfCollector
from lenticularis.collectors.windline import WindlineCollector
from lenticularis.collectors.wunderground import WundergroundCollector
from lenticularis.config import MainConfig

if TYPE_CHECKING:
    from lenticularis.database.influx import InfluxClient
    from lenticularis.models.weather import WeatherStation

logger = logging.getLogger(__name__)

# Registry: observation collector name → class
_COLLECTOR_REGISTRY = {
    "meteoswiss": MeteoSwissCollector,
    "slf": SlfCollector,
    "metar": MetarCollector,
    "windline": WindlineCollector,
    "ecowitt": EcowittCollector,
    "wunderground": WundergroundCollector,
}

# Registry: forecast collector name → class
_FORECAST_REGISTRY = {
    "open-meteo": ForecastOpenMeteoCollector,
}

# Jitter range in seconds applied to each job's first run
_JITTER_SECONDS = 60


def get_collector_class(name: str):
    """Return an observation collector class by configured name, or ``None`` if unknown."""
    return _COLLECTOR_REGISTRY.get(name)


class CollectorScheduler:
    """
    Wraps APScheduler and manages the lifecycle of all collector jobs.

    Usage::

        scheduler = CollectorScheduler(cfg, influx_client, station_registry)
        await scheduler.start()
        ...
        await scheduler.stop()

    ``station_registry`` is the shared dict (populated by observation collectors)
    that forecast collectors use to discover station lat/lon.  Pass the same
    dict reference that ``app.state.station_registry`` holds.
    """

    def __init__(
        self,
        cfg: MainConfig,
        influx: "InfluxClient",
        station_registry: Optional[dict] = None,
    ) -> None:
        self._cfg = cfg
        self._influx = influx
        self._station_registry: dict = station_registry if station_registry is not None else {}
        self._scheduler = AsyncIOScheduler()
        self._collectors: list = []
        self._forecast_collectors: list = []
        self._collector_health: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        """Instantiate collectors, register interval jobs, and start the scheduler."""
        # ---- observation collectors ----------------------------------------
        for collector_cfg in self._cfg.collectors:
            self._collector_health[collector_cfg.name] = {
                "collector": collector_cfg.name,
                "type": "observation",
                "enabled": collector_cfg.enabled,
                "interval_minutes": collector_cfg.interval_minutes,
                "status": "disabled" if not collector_cfg.enabled else "pending",
                "last_started_at": None,
                "last_finished_at": None,
                "last_success_at": None,
                "last_error_at": None,
                "last_error": None,
                "last_measurement_count": None,
                "consecutive_failures": 0,
            }

            if not collector_cfg.enabled:
                logger.info("Collector '%s' is disabled — skipping", collector_cfg.name)
                continue

            cls = _COLLECTOR_REGISTRY.get(collector_cfg.name)
            if cls is None:
                logger.warning("Unknown collector '%s' — no implementation found", collector_cfg.name)
                self._collector_health[collector_cfg.name]["status"] = "unknown_collector"
                continue

            collector = cls(config=collector_cfg.config)
            self._collectors.append(collector)

            self._scheduler.add_job(
                func=self._run_collector,
                trigger=IntervalTrigger(minutes=collector_cfg.interval_minutes),
                args=[collector],
                id=f"collector_{collector_cfg.name}",
                name=f"{collector_cfg.name} collector",
                misfire_grace_time=180,
                coalesce=True,
                max_instances=1,
                next_run_time=None,
            )
            self._collector_health[collector_cfg.name]["status"] = "scheduled"
            logger.info(
                "Registered collector '%s' with %d-minute interval",
                collector_cfg.name,
                collector_cfg.interval_minutes,
            )

        # ---- forecast collectors -------------------------------------------
        for fc_cfg in self._cfg.forecast_collectors:
            health_key = f"forecast_{fc_cfg.name}"
            self._collector_health[health_key] = {
                "collector": fc_cfg.name,
                "type": "forecast",
                "enabled": fc_cfg.enabled,
                "interval_minutes": fc_cfg.interval_minutes,
                "horizon_hours": fc_cfg.horizon_hours,
                "status": "disabled" if not fc_cfg.enabled else "pending",
                "last_started_at": None,
                "last_finished_at": None,
                "last_success_at": None,
                "last_error_at": None,
                "last_error": None,
                "last_measurement_count": None,
                "consecutive_failures": 0,
            }

            if not fc_cfg.enabled:
                logger.info("Forecast collector '%s' is disabled — skipping", fc_cfg.name)
                continue

            cls = _FORECAST_REGISTRY.get(fc_cfg.name)
            if cls is None:
                logger.warning("Unknown forecast collector '%s' — no implementation found", fc_cfg.name)
                self._collector_health[health_key]["status"] = "unknown_collector"
                continue

            fc = cls(config=fc_cfg.config)
            self._forecast_collectors.append((fc, fc_cfg))

            self._scheduler.add_job(
                func=self._run_forecast_collector,
                trigger=IntervalTrigger(minutes=fc_cfg.interval_minutes),
                args=[fc, fc_cfg.horizon_hours],
                id=f"forecast_{fc_cfg.name}",
                name=f"{fc_cfg.name} forecast collector",
                misfire_grace_time=300,
                coalesce=True,
                max_instances=1,
                next_run_time=None,
            )
            self._collector_health[health_key]["status"] = "scheduled"
            logger.info(
                "Registered forecast collector '%s' with %d-minute interval, %dh horizon",
                fc_cfg.name,
                fc_cfg.interval_minutes,
                fc_cfg.horizon_hours,
            )

        # ---- föhn status collector -----------------------------------------
        self._foehn_collector = FoehnCollector()
        self._collector_health["foehn"] = {
            "collector": "foehn",
            "type": "derived",
            "enabled": True,
            "interval_minutes": 10,
            "status": "scheduled",
            "last_started_at": None,
            "last_finished_at": None,
            "last_success_at": None,
            "last_error_at": None,
            "last_error": None,
            "last_measurement_count": None,
            "consecutive_failures": 0,
        }
        self._scheduler.add_job(
            func=self._run_foehn_collector,
            trigger=IntervalTrigger(minutes=10),
            id="collector_foehn",
            name="foehn status collector",
            misfire_grace_time=180,
            coalesce=True,
            max_instances=1,
            next_run_time=None,
        )
        logger.info("Registered foehn status collector with 10-minute interval")

        self._scheduler.start()

        # Trigger first runs with jitter
        for collector_cfg in self._cfg.collectors:
            if collector_cfg.enabled and collector_cfg.name in _COLLECTOR_REGISTRY:
                jitter_secs = random.uniform(1, _JITTER_SECONDS)
                asyncio.get_event_loop().call_later(
                    jitter_secs,
                    lambda name=collector_cfg.name: asyncio.ensure_future(
                        self._trigger_now(f"collector_{name}")
                    ),
                )

        for fc_cfg in self._cfg.forecast_collectors:
            if fc_cfg.enabled and fc_cfg.name in _FORECAST_REGISTRY:
                # Forecast collectors start after a longer delay so the station
                # registry is populated by observation collectors first
                jitter_secs = random.uniform(_JITTER_SECONDS, _JITTER_SECONDS * 3)
                asyncio.get_event_loop().call_later(
                    jitter_secs,
                    lambda name=fc_cfg.name: asyncio.ensure_future(
                        self._trigger_now(f"forecast_{name}")
                    ),
                )

        # Foehn collector starts after observation collectors have populated data
        asyncio.get_event_loop().call_later(
            random.uniform(_JITTER_SECONDS, _JITTER_SECONDS * 2),
            lambda: asyncio.ensure_future(self._trigger_now("collector_foehn")),
        )

        logger.info(
            "Scheduler started — %d observation collector(s), %d forecast collector(s), föhn collector",
            len(self._collectors),
            len(self._forecast_collectors),
        )

    async def _trigger_now(self, job_id: str) -> None:
        """Manually fire a job by its ID (used for initial jittered run)."""
        job = self._scheduler.get_job(job_id)
        if job:
            job.modify(next_run_time=datetime.now(timezone.utc))

    async def _run_foehn_collector(self) -> None:
        """Evaluate föhn regions and write results to InfluxDB."""
        health = self._collector_health["foehn"]
        health["status"] = "running"
        health["last_started_at"] = datetime.now(timezone.utc)
        try:
            count = await self._foehn_collector.run(self._influx, self._station_registry)
            finished_at = datetime.now(timezone.utc)
            health.update({
                "status": "ok",
                "last_finished_at": finished_at,
                "last_success_at": finished_at,
                "last_error": None,
                "last_measurement_count": count,
                "consecutive_failures": 0,
            })
            logger.info("FoehnCollector: wrote %d points", count)
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            health.update({
                "status": "error",
                "last_finished_at": finished_at,
                "last_error_at": finished_at,
                "last_error": str(exc),
                "consecutive_failures": int(health.get("consecutive_failures", 0)) + 1,
            })
            logger.error("FoehnCollector failed: %s", exc)

    async def _run_collector(self, collector) -> None:
        """Execute a single observation collector run and write results to InfluxDB."""
        name = collector.__class__.__name__
        collector_name = getattr(collector, "NETWORK", name.lower())
        health = self._collector_health.setdefault(
            collector_name,
            {
                "collector": collector_name,
                "type": "observation",
                "enabled": True,
                "interval_minutes": None,
                "status": "pending",
                "last_started_at": None,
                "last_finished_at": None,
                "last_success_at": None,
                "last_error_at": None,
                "last_error": None,
                "last_measurement_count": None,
                "consecutive_failures": 0,
            },
        )
        started_at = datetime.now(timezone.utc)
        health["status"] = "running"
        health["last_started_at"] = started_at

        logger.info("Running collector: %s", name)
        try:
            measurements = await collector.collect()
            if measurements:
                self._influx.write_measurements(measurements)
                logger.info("%s: wrote %d measurements", name, len(measurements))
                health["status"] = "ok"
            else:
                logger.info("%s: no measurements returned", name)
                health["status"] = "ok_no_data"

            finished_at = datetime.now(timezone.utc)
            health["last_finished_at"] = finished_at
            health["last_success_at"] = finished_at
            health["last_error_at"] = None
            health["last_error"] = None
            health["last_measurement_count"] = len(measurements)
            health["consecutive_failures"] = 0
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            health["status"] = "error"
            health["last_finished_at"] = finished_at
            health["last_error_at"] = finished_at
            health["last_error"] = str(exc)
            health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
            logger.error("Collector %s failed: %s", name, exc, exc_info=True)

    async def _run_forecast_collector(self, collector, horizon_hours: int) -> None:
        """Execute a single forecast collector run and write results to InfluxDB."""
        name = collector.__class__.__name__
        health_key = f"forecast_{collector.SOURCE}"
        health = self._collector_health.setdefault(health_key, {
            "collector": collector.SOURCE,
            "type": "forecast",
            "enabled": True,
            "interval_minutes": None,
            "status": "pending",
            "last_started_at": None,
            "last_finished_at": None,
            "last_success_at": None,
            "last_error_at": None,
            "last_error": None,
            "last_measurement_count": None,
            "consecutive_failures": 0,
        })

        started_at = datetime.now(timezone.utc)
        health["status"] = "running"
        health["last_started_at"] = started_at

        stations = list(self._station_registry.values())
        stations_with_coords = [
            s for s in stations
            if getattr(s, "latitude", None) is not None
            and getattr(s, "longitude", None) is not None
        ]

        if not stations_with_coords:
            logger.warning(
                "Forecast collector %s: no stations with coordinates in registry — "
                "will retry when observation collectors have run",
                name,
            )
            health["status"] = "ok_no_stations"
            health["last_finished_at"] = datetime.now(timezone.utc)
            health["last_measurement_count"] = 0
            return

        logger.info(
            "Running forecast collector: %s for %d stations, horizon %dh",
            name, len(stations_with_coords), horizon_hours,
        )
        try:
            points = await collector.collect_all(stations_with_coords, horizon_hours)
            if points:
                self._influx.write_forecast(points)
                logger.info("%s: wrote %d forecast points", name, len(points))
                health["status"] = "ok"
            else:
                logger.info("%s: no forecast points returned", name)
                health["status"] = "ok_no_data"

            finished_at = datetime.now(timezone.utc)
            health["last_finished_at"] = finished_at
            health["last_success_at"] = finished_at
            health["last_error_at"] = None
            health["last_error"] = None
            health["last_measurement_count"] = len(points)
            health["consecutive_failures"] = 0
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            health["status"] = "error"
            health["last_finished_at"] = finished_at
            health["last_error_at"] = finished_at
            health["last_error"] = str(exc)
            health["consecutive_failures"] = int(health.get("consecutive_failures", 0)) + 1
            logger.error("Forecast collector %s failed: %s", name, exc, exc_info=True)

    def update_collector_runtime(
        self,
        health_key: str,
        enabled: bool | None = None,
        interval_minutes: int | None = None,
    ) -> dict[str, Any]:
        """
        Toggle or reschedule a collector at runtime.
        Changes are in-memory only and reset on restart.

        health_key: the key used in _collector_health (e.g. 'meteoswiss', 'forecast_open-meteo').
        """
        if health_key not in self._collector_health:
            raise KeyError(f"Unknown collector: {health_key!r}")

        health = self._collector_health[health_key]

        if health.get("type") == "derived":
            raise ValueError("The foehn collector cannot be toggled at runtime")

        # Resolve APScheduler job ID from health entry type
        ctype = health.get("type")
        if ctype == "forecast":
            job_id = health_key                         # e.g. "forecast_open-meteo"
        else:
            job_id = f"collector_{health_key}"          # e.g. "collector_meteoswiss"

        job = self._scheduler.get_job(job_id)

        if enabled is not None:
            health["enabled"] = enabled
            if enabled:
                if job:
                    job.resume()
                health["status"] = "scheduled"
            else:
                if job:
                    job.pause()
                health["status"] = "disabled"

        if interval_minutes is not None and interval_minutes > 0:
            health["interval_minutes"] = interval_minutes
            if job:
                job.reschedule(trigger=IntervalTrigger(minutes=interval_minutes))

        # Return a serialised snapshot (mirrors get_collector_health row shape)
        row = dict(health)
        job = self._scheduler.get_job(job_id)
        row["next_run_time"] = job.next_run_time if job else None
        return row

    def get_collector_health(self) -> list[dict[str, Any]]:
        """Return a health snapshot for all configured collectors."""
        snapshot: list[dict[str, Any]] = []
        for collector_name, health in self._collector_health.items():
            row = dict(health)
            job = self._scheduler.get_job(f"collector_{collector_name}") or \
                  self._scheduler.get_job(f"forecast_{health.get('collector', '')}")
            row["next_run_time"] = job.next_run_time if job else None
            snapshot.append(row)
        snapshot.sort(key=lambda r: (r.get("type", ""), str(r.get("collector", ""))))
        return snapshot

    async def stop(self) -> None:
        """Shut down the scheduler and close all collector HTTP clients."""
        self._scheduler.shutdown(wait=False)
        for collector in self._collectors:
            await collector.close()
        for collector, _ in self._forecast_collectors:
            await collector.close()
        logger.info("Scheduler stopped")

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler
