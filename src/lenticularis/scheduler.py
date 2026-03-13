"""
APScheduler wiring for Lenticularis.

Schedules all enabled collectors and submits their results to InfluxDB.
A small random jitter is applied to each job's first run to prevent
every collector from firing simultaneously at startup.
"""

from __future__ import annotations

import asyncio
import logging
import random
from datetime import datetime, timedelta, timezone
from typing import TYPE_CHECKING, Any

from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger

from lenticularis.collectors.ecowitt import EcowittCollector
from lenticularis.collectors.metar import MetarCollector
from lenticularis.collectors.meteoswiss import MeteoSwissCollector
from lenticularis.collectors.slf import SlfCollector
from lenticularis.collectors.windline import WindlineCollector
from lenticularis.config import MainConfig

if TYPE_CHECKING:
    from lenticularis.database.influx import InfluxClient

logger = logging.getLogger(__name__)

# Registry: collector name → class
_COLLECTOR_REGISTRY = {
    "meteoswiss": MeteoSwissCollector,
    "slf": SlfCollector,
    "metar": MetarCollector,
    "windline": WindlineCollector,
    "ecowitt": EcowittCollector,
}

# Jitter range in seconds applied to each job's first run
_JITTER_SECONDS = 60


def get_collector_class(name: str):
    """Return a collector class by configured name, or ``None`` if unknown."""
    return _COLLECTOR_REGISTRY.get(name)


class CollectorScheduler:
    """
    Wraps APScheduler and manages the lifecycle of all collector jobs.

    Usage::

        scheduler = CollectorScheduler(cfg, influx_client)
        await scheduler.start()
        ...
        await scheduler.stop()
    """

    def __init__(self, cfg: MainConfig, influx: "InfluxClient") -> None:
        self._cfg = cfg
        self._influx = influx
        self._scheduler = AsyncIOScheduler()
        self._collectors: list = []
        self._collector_health: dict[str, dict[str, Any]] = {}

    async def start(self) -> None:
        """Instantiate collectors, register interval jobs, and start the scheduler."""
        for collector_cfg in self._cfg.collectors:
            self._collector_health[collector_cfg.name] = {
                "collector": collector_cfg.name,
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

            # Stagger first run with a random jitter
            jitter = timedelta(seconds=random.randint(0, _JITTER_SECONDS))

            self._scheduler.add_job(
                func=self._run_collector,
                trigger=IntervalTrigger(minutes=collector_cfg.interval_minutes),
                args=[collector],
                id=f"collector_{collector_cfg.name}",
                name=f"{collector_cfg.name} collector",
                misfire_grace_time=180,
                coalesce=True,
                max_instances=1,
                next_run_time=None,  # will be set after scheduler starts + jitter
            )
            self._collector_health[collector_cfg.name]["status"] = "scheduled"
            logger.info(
                "Registered collector '%s' with %d-minute interval (jitter +%ds)",
                collector_cfg.name,
                collector_cfg.interval_minutes,
                jitter.seconds,
            )

        self._scheduler.start()

        # Trigger first run for each collector after a short jitter
        for collector_cfg in self._cfg.collectors:
            if collector_cfg.enabled and collector_cfg.name in _COLLECTOR_REGISTRY:
                jitter_secs = random.uniform(1, _JITTER_SECONDS)
                asyncio.get_event_loop().call_later(
                    jitter_secs,
                    lambda name=collector_cfg.name: asyncio.ensure_future(
                        self._trigger_now(name)
                    ),
                )

        logger.info("Scheduler started with %d active collector(s)", len(self._collectors))

    async def _trigger_now(self, job_id: str) -> None:
        """Manually fire a job by its ID (used for initial jittered run)."""
        job = self._scheduler.get_job(f"collector_{job_id}")
        if job:
            job.modify(next_run_time=__import__("datetime").datetime.now(
                __import__("datetime").timezone.utc
            ))

    async def _run_collector(self, collector) -> None:
        """Execute a single collector run and write results to InfluxDB."""
        name = collector.__class__.__name__
        collector_name = getattr(collector, "NETWORK", name.lower())
        health = self._collector_health.setdefault(
            collector_name,
            {
                "collector": collector_name,
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

    def get_collector_health(self) -> list[dict[str, Any]]:
        """Return a health snapshot for all configured collectors."""
        snapshot: list[dict[str, Any]] = []
        for collector_name, health in self._collector_health.items():
            row = dict(health)
            job = self._scheduler.get_job(f"collector_{collector_name}")
            row["next_run_time"] = job.next_run_time if job else None
            snapshot.append(row)

        snapshot.sort(key=lambda r: str(r.get("collector", "")))
        return snapshot

    async def stop(self) -> None:
        """Shut down the scheduler and close all collector HTTP clients."""
        self._scheduler.shutdown(wait=False)
        for collector in self._collectors:
            await collector.close()
        logger.info("Scheduler stopped")

    @property
    def scheduler(self) -> AsyncIOScheduler:
        return self._scheduler
