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
from lenticularis.collectors.fga import FgaCollector
from lenticularis.collectors.foehn import FoehnCollector
from lenticularis.collectors.forecast_grid import ForecastGridCollector
from lenticularis.collectors.forecast_grid_swissmeteo import ForecastGridSwissMeteoCollector
from lenticularis.collectors.forecast_openmeteo import ForecastOpenMeteoCollector
from lenticularis.collectors.forecast_swissmeteo import ForecastSwissMeteoCollector
from lenticularis.collectors.holfuy import HolfuyCollector
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
    "holfuy": HolfuyCollector,
    "slf": SlfCollector,
    "metar": MetarCollector,
    "windline": WindlineCollector,
    "ecowitt": EcowittCollector,
    "wunderground": WundergroundCollector,
    "fga": FgaCollector,
}

# Registry: forecast collector name → class
_FORECAST_REGISTRY = {
    "open-meteo":       ForecastOpenMeteoCollector,
    "open-meteo-short": ForecastOpenMeteoCollector,  # same class, short-horizon frequent refreshes
    "swissmeteo":       ForecastSwissMeteoCollector,
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

    ``session_factory`` is the SQLAlchemy session factory from ``db.get_session_factory()``.
    When provided, a periodic ruleset evaluation job is registered that writes
    ``rule_decisions`` to InfluxDB every 10 minutes — populating the history strip
    on the org dashboard and personal ruleset analysis pages.
    """

    def __init__(
        self,
        cfg: MainConfig,
        influx: "InfluxClient",
        station_registry: Optional[dict] = None,
        session_factory=None,
        virtual_members: Optional[dict] = None,
    ) -> None:
        self._cfg = cfg
        self._influx = influx
        self._station_registry: dict = station_registry if station_registry is not None else {}
        self._virtual_members: dict = virtual_members if virtual_members is not None else {}
        self._session_factory = session_factory
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

            # SwissMeteo also provides altitude wind profiles — register a companion job
            if fc_cfg.name == "swissmeteo" and isinstance(fc, ForecastSwissMeteoCollector):
                alt_health_key = "forecast_swissmeteo_altitude"
                self._collector_health[alt_health_key] = {
                    "collector": "swissmeteo_altitude",
                    "type": "forecast",
                    "enabled": True,
                    "interval_minutes": fc_cfg.interval_minutes,
                    "horizon_hours": fc_cfg.horizon_hours,
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
                    func=self._run_swissmeteo_altitude_collector,
                    trigger=IntervalTrigger(minutes=fc_cfg.interval_minutes),
                    args=[fc, fc_cfg.horizon_hours],
                    id="forecast_swissmeteo_altitude",
                    name="swissmeteo altitude wind profile collector",
                    misfire_grace_time=300,
                    coalesce=True,
                    max_instances=1,
                    next_run_time=None,
                )
                logger.info(
                    "Registered swissmeteo altitude wind profile collector (%d-minute interval)",
                    fc_cfg.interval_minutes,
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

        # ---- wind forecast grid collector -----------------------------------
        self._collector_health["forecast_grid"] = {
            "collector": "grid",
            "type": "forecast",
            "enabled": True,
            "interval_minutes": 180,
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
            func=self._run_grid_forecast_collector,
            trigger=IntervalTrigger(minutes=180),
            id="forecast_grid",
            name="wind forecast grid collector",
            misfire_grace_time=600,
            coalesce=True,
            max_instances=1,
            next_run_time=None,
        )
        logger.info("Registered wind forecast grid collector with 180-minute interval")

        # ---- ruleset evaluator (writes rule_decisions to InfluxDB) ----------
        if self._session_factory is not None:
            self._collector_health["ruleset_evaluator"] = {
                "collector": "ruleset_evaluator",
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
                func=self._run_ruleset_evaluator,
                trigger=IntervalTrigger(minutes=10),
                id="collector_ruleset_evaluator",
                name="ruleset evaluator",
                misfire_grace_time=180,
                coalesce=True,
                max_instances=1,
                next_run_time=None,
            )
            logger.info("Registered ruleset evaluator with 10-minute interval")

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
                if fc_cfg.name == "swissmeteo":
                    asyncio.get_event_loop().call_later(
                        jitter_secs + 5,
                        lambda: asyncio.ensure_future(
                            self._trigger_now("forecast_swissmeteo_altitude")
                        ),
                    )

        # Grid forecast collector starts after a longer delay (doesn't need stations)
        asyncio.get_event_loop().call_later(
            random.uniform(_JITTER_SECONDS * 2, _JITTER_SECONDS * 4),
            lambda: asyncio.ensure_future(self._trigger_now("forecast_grid")),
        )

        # Foehn collector starts after observation collectors have populated data
        asyncio.get_event_loop().call_later(
            random.uniform(_JITTER_SECONDS, _JITTER_SECONDS * 2),
            lambda: asyncio.ensure_future(self._trigger_now("collector_foehn")),
        )

        # Ruleset evaluator starts after collectors have written fresh data
        if self._session_factory is not None:
            asyncio.get_event_loop().call_later(
                random.uniform(_JITTER_SECONDS * 2, _JITTER_SECONDS * 3),
                lambda: asyncio.ensure_future(self._trigger_now("collector_ruleset_evaluator")),
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

    async def trigger_collector_now(self, health_key: str) -> None:
        """Immediately queue a collector run by health_key."""
        if health_key not in self._collector_health:
            raise KeyError(f"Unknown collector: {health_key!r}")
        health = self._collector_health[health_key]
        ctype  = health.get("type")
        if ctype == "forecast":
            job_id = health_key
        elif ctype == "derived":
            job_id = "collector_foehn"
        else:
            job_id = f"collector_{health_key}"
        await self._trigger_now(job_id)

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

    async def _run_ruleset_evaluator(self) -> None:
        """Evaluate all rulesets with conditions and write decisions to InfluxDB."""
        health = self._collector_health.get("ruleset_evaluator")
        if health:
            health["status"] = "running"
            health["last_started_at"] = datetime.now(timezone.utc)

        db = self._session_factory()
        count = 0
        try:
            from sqlalchemy import select
            from lenticularis.database.models import RuleSet, User
            from lenticularis.rules.evaluator import run_evaluation, write_decision

            rulesets = db.execute(select(RuleSet)).scalars().all()
            for rs in rulesets:
                if not rs.conditions:
                    continue
                try:
                    result = run_evaluation(rs, self._influx, self._virtual_members)
                    write_decision(rs, result, self._influx)
                    count += 1
                    self._maybe_notify(rs, result["decision"], db)
                except Exception as exc:
                    logger.error("Ruleset evaluator: failed to evaluate %s: %s", rs.id, exc)

            finished_at = datetime.now(timezone.utc)
            if health:
                health.update({
                    "status": "ok",
                    "last_finished_at": finished_at,
                    "last_success_at": finished_at,
                    "last_error": None,
                    "last_measurement_count": count,
                    "consecutive_failures": 0,
                })
            logger.info("Ruleset evaluator: evaluated %d rulesets", count)
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            if health:
                health.update({
                    "status": "error",
                    "last_finished_at": finished_at,
                    "last_error_at": finished_at,
                    "last_error": str(exc),
                    "consecutive_failures": int(health.get("consecutive_failures", 0)) + 1,
                })
            logger.error("Ruleset evaluator job failed: %s", exc)
        finally:
            db.close()

    def _maybe_notify(self, rs, decision: str, db) -> None:
        """
        Send an email notification if the ruleset decision changed and the
        new decision matches the user's ``notify_on`` preference.

        Updates ``rs.last_notified_decision`` in-place and commits to SQLite.
        """
        if not rs.notify_on:
            return

        notify_colours = {c.strip() for c in rs.notify_on.split(",") if c.strip()}
        if decision not in notify_colours:
            return
        if decision == rs.last_notified_decision:
            return  # No state change — suppress repeat notification

        smtp_cfg = self._cfg.smtp
        if not smtp_cfg.enabled:
            logger.debug(
                "Notification suppressed (SMTP disabled) for ruleset %s → %s", rs.id, decision
            )
            return

        try:
            from lenticularis.database.models import User
            owner = db.get(User, rs.owner_id)
            if owner is None or not owner.email:
                logger.warning("Cannot notify: owner %s has no email (ruleset %s)", rs.owner_id, rs.id)
                return

            colour_emoji = {"green": "🟢", "orange": "🟠", "red": "🔴"}.get(decision, "")
            subject = f"{colour_emoji} {rs.name} — {decision.upper()}"

            body_text = (
                f"Your ruleset '{rs.name}' just evaluated {decision.upper()}.\n\n"
                f"View analysis: {smtp_cfg.from_address and 'https://lenti.lg4.ch'}/ruleset-analysis?id={rs.id}\n\n"
                f"— Lenticularis"
            )
            body_html = f"""<!DOCTYPE html>
<html><body style="font-family:sans-serif;background:#0f1117;color:#e2e8f0;padding:32px">
  <h2 style="color:#{'68d391' if decision=='green' else 'f6ad55' if decision=='orange' else 'fc8181'}">{colour_emoji} {rs.name}</h2>
  <p style="font-size:1.1rem">Current decision: <strong style="color:#{'68d391' if decision=='green' else 'f6ad55' if decision=='orange' else 'fc8181'}">{decision.upper()}</strong></p>
  <p><a href="https://lenti.lg4.ch/ruleset-analysis?id={rs.id}" style="color:#90cdf4">View full analysis →</a></p>
  <hr style="border-color:#2d3748;margin:24px 0"/>
  <p style="font-size:0.8rem;color:#4a5568">You are receiving this because you enabled email notifications for this ruleset.<br/>
  Edit your notification preferences in the <a href="https://lenti.lg4.ch/ruleset-editor?id={rs.id}" style="color:#4a5568">ruleset editor</a>.</p>
</body></html>"""

            from lenticularis.utils.mailer import send_email
            sent = send_email(smtp_cfg, owner.email, subject, body_text, body_html)
            if sent:
                rs.last_notified_decision = decision
                db.commit()
                logger.info(
                    "Notification sent for ruleset %s → %s (to %s)", rs.id, decision, owner.email
                )
        except Exception as exc:
            logger.error("Notification failed for ruleset %s: %s", rs.id, exc)

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

        interval_minutes = health.get("interval_minutes") or 60
        spread_seconds = interval_minutes * 60.0
        logger.info(
            "Running forecast collector: %s for %d stations, horizon %dh, spread %.0fs",
            name, len(stations_with_coords), horizon_hours, spread_seconds,
        )

        total_points = 0
        errors = 0
        try:
            async for station, pts in collector.collect_all_iter(
                stations_with_coords, horizon_hours, spread_seconds=spread_seconds
            ):
                if pts:
                    self._influx.write_forecast(pts)
                    total_points += len(pts)
                    logger.debug("%s: wrote %d points for %s", name, len(pts), station.station_id)
                else:
                    errors += 1

            finished_at = datetime.now(timezone.utc)
            health.update({
                "status": "ok" if total_points > 0 else ("error" if errors > 0 else "ok_no_data"),
                "last_finished_at": finished_at,
                "last_success_at": finished_at if total_points > 0 else health.get("last_success_at"),
                "last_error_at": None if total_points > 0 else finished_at,
                "last_error": None if total_points > 0 else f"0 points written, {errors} station errors",
                "last_measurement_count": total_points,
                "consecutive_failures": 0 if total_points > 0 else int(health.get("consecutive_failures", 0)) + 1,
            })
            logger.info("%s: wrote %d forecast points total (%d station errors)", name, total_points, errors)
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            health.update({
                "status": "error",
                "last_finished_at": finished_at,
                "last_error_at": finished_at,
                "last_error": str(exc),
                "consecutive_failures": int(health.get("consecutive_failures", 0)) + 1,
            })
            logger.error("Forecast collector %s failed: %s", name, exc, exc_info=True)

    async def _run_grid_forecast_collector(self) -> None:
        """Collect wind forecasts for the 0.25° Switzerland grid and write to InfluxDB."""
        health = self._collector_health.setdefault("forecast_grid", {
            "collector": "grid",
            "type": "forecast",
            "enabled": True,
            "interval_minutes": 180,
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
        logger.info("[Lenti:grid-collector] Starting grid forecast collector run")

        # lsmfapi base_url — reuse the swissmeteo forecast collector config
        base_url = "https://lsmfapi-dev.lg4.ch"
        for fc_cfg in self._cfg.forecast_collectors:
            if fc_cfg.name == "swissmeteo":
                base_url = (fc_cfg.config or {}).get("base_url", base_url)
                break

        # Open-Meteo API key — fallback only
        api_key: str | None = None
        for fc_cfg in self._cfg.forecast_collectors:
            k = (fc_cfg.config or {}).get("api_key")
            if k:
                api_key = k
                break

        points: list = []
        source = "none"
        last_error: str | None = None

        # Primary: SwissMeteo (lsmfapi)
        sm_collector = ForecastGridSwissMeteoCollector(base_url=base_url)
        try:
            points = await sm_collector.collect_all(horizon_hours=120)
            if points:
                source = "swissmeteo"
            else:
                logger.warning("[Lenti:grid-collector] SwissMeteo grid returned 0 points — trying Open-Meteo fallback")
        except Exception as exc:
            last_error = str(exc)
            logger.warning("[Lenti:grid-collector] SwissMeteo grid failed: %s — trying Open-Meteo fallback", exc)
        finally:
            await sm_collector.close()

        # Fallback: Open-Meteo
        if not points:
            om_collector = ForecastGridCollector(api_key=api_key)
            if api_key:
                logger.info("[Lenti:grid-collector] Using commercial Open-Meteo API key")
            try:
                points = await om_collector.collect_all(horizon_hours=120)
                if points:
                    source = "open-meteo"
                    last_error = None
            except Exception as exc:
                last_error = str(exc)
                logger.error("[Lenti:grid-collector] Open-Meteo fallback also failed: %s", exc, exc_info=True)
            finally:
                await om_collector.close()

        try:
            if points:
                self._influx.write_forecast_grid(points)

            finished_at = datetime.now(timezone.utc)
            health.update({
                "status": "ok" if points else "error",
                "last_finished_at": finished_at,
                "last_success_at": finished_at if points else health.get("last_success_at"),
                "last_error_at": None if points else finished_at,
                "last_error": last_error if not points else None,
                "last_measurement_count": len(points),
                "consecutive_failures": 0 if points else int(health.get("consecutive_failures", 0)) + 1,
            })
            logger.info(
                "[Lenti:grid-collector] Run complete — wrote %d grid forecast points (source: %s)",
                len(points), source,
            )
        except Exception as exc:
            finished_at = datetime.now(timezone.utc)
            health.update({
                "status": "error",
                "last_finished_at": finished_at,
                "last_error_at": finished_at,
                "last_error": str(exc),
                "consecutive_failures": int(health.get("consecutive_failures", 0)) + 1,
            })
            logger.error("[Lenti:grid-collector] InfluxDB write failed: %s", exc, exc_info=True)

    async def _run_swissmeteo_altitude_collector(
        self,
        collector: ForecastSwissMeteoCollector,
        horizon_hours: int,
    ) -> None:
        """Collect altitude wind profiles from the SwissMeteo container and write to InfluxDB."""
        health = self._collector_health.setdefault("forecast_swissmeteo_altitude", {
            "collector": "swissmeteo_altitude",
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

        stations = [
            s for s in self._station_registry.values()
            if getattr(s, "latitude", None) is not None
            and getattr(s, "longitude", None) is not None
        ]
        if not stations:
            health.update({"status": "ok_no_stations", "last_finished_at": datetime.now(timezone.utc), "last_measurement_count": 0})
            return

        logger.info("Running SwissMeteo altitude collector for %d stations, horizon %dh", len(stations), horizon_hours)

        total_points = 0
        errors = 0
        for station in stations:
            try:
                pts = await collector.collect_altitude_for_station(
                    station.station_id, station.network, horizon_hours
                )
                if pts:
                    self._influx.write_station_wind_profile(pts)
                    total_points += len(pts)
                else:
                    errors += 1
            except Exception as exc:
                errors += 1
                logger.error("Altitude collection failed for %s: %s", station.station_id, exc)

        finished_at = datetime.now(timezone.utc)
        health.update({
            "status": "ok" if total_points > 0 else "ok_no_data",
            "last_finished_at": finished_at,
            "last_success_at": finished_at,
            "last_error_at": None,
            "last_error": None,
            "last_measurement_count": total_points,
            "consecutive_failures": 0,
        })
        logger.info("SwissMeteo altitude: wrote %d points total (%d station errors)", total_points, errors)

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
