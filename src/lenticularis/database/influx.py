"""
InfluxDB 2.x client for Lenticularis.

Provides:
  - ``write_measurements()``  — write a batch of WeatherMeasurement rows
  - ``query_latest()``        — fetch the most recent measurement for a station
  - ``query_history()``       — fetch time-series data for a station
  - ``has_measure()``         — idempotency guard (was this timestamp already written?)
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta, timezone
from typing import Optional

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from lenticularis.config import InfluxDBConfig
from lenticularis.models.weather import ForecastPoint, WeatherMeasurement

logger = logging.getLogger(__name__)

# InfluxDB measurement names
MEASUREMENT_WEATHER = "weather_data"
MEASUREMENT_FORECAST = "weather_forecast"


class InfluxClient:
    """
    Thin wrapper around the official ``influxdb-client`` library.

    All writes are synchronous (SYNCHRONOUS write option) because the
    influxdb-client async API requires separate handling; for simplicity we
    keep the collector coroutines async but do the actual write call synchronously
    inside an executor if latency becomes a concern. In practice MeteoSwiss data
    arrives every 10 minutes so synchronous writes are fine.
    """

    def __init__(self, cfg: InfluxDBConfig) -> None:
        self._cfg = cfg
        self._client = InfluxDBClient(
            url=cfg.url,
            token=cfg.token,
            org=cfg.org,
            timeout=cfg.timeout,
        )
        self._write_api = self._client.write_api(write_options=SYNCHRONOUS)
        self._query_api = self._client.query_api()
        logger.info("InfluxDB client initialised — %s / %s", cfg.url, cfg.bucket)

    # ------------------------------------------------------------------
    # Write
    # ------------------------------------------------------------------

    def write_measurements(self, measurements: list[WeatherMeasurement]) -> None:
        """
        Write a batch of ``WeatherMeasurement`` objects to InfluxDB.

        Each measurement becomes one Point in the ``weather_data`` measurement.
        ``None`` field values are silently omitted (InfluxDB ignores missing fields).
        """
        if not measurements:
            return

        points: list[Point] = []
        for m in measurements:
            p = (
                Point(MEASUREMENT_WEATHER)
                .tag("station_id", m.station_id)
                .tag("network", m.network)
                .time(m.timestamp, "s")
            )
            # Optional fields — only write fields that have a value
            field_map = {
                "wind_speed": m.wind_speed,
                "wind_direction": m.wind_direction,
                "wind_gust": m.wind_gust,
                "temperature": m.temperature,
                "humidity": m.humidity,
                "pressure_qfe": m.pressure_qfe,
                "pressure_qnh": m.pressure_qnh,
                "pressure_qff": m.pressure_qff,
                "precipitation": m.precipitation,
                "snow_depth": m.snow_depth,
            }
            for field_name, value in field_map.items():
                if value is not None:
                    p = p.field(field_name, float(value))
            points.append(p)

        try:
            self._write_api.write(bucket=self._cfg.bucket, org=self._cfg.org, record=points)
            logger.debug("Wrote %d points to InfluxDB", len(points))
        except Exception as exc:
            logger.error("InfluxDB write error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Query — latest measurement
    # ------------------------------------------------------------------

    def query_latest(self, station_id: str) -> Optional[dict]:
        """
        Return the most-recent field values for ``station_id`` as a plain dict,
        or ``None`` if no data exists.

        Example return value::

            {
                "timestamp": datetime(...),
                "wind_speed": 14.2,
                "temperature": 3.1,
                ...
            }
        """
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => r.station_id == "{station_id}")
  |> last()
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB query error for %s: %s", station_id, exc)
            return None

        for table in tables:
            for record in table.records:
                result: dict = {"timestamp": record.get_time()}
                result.update({k: v for k, v in record.values.items() if not k.startswith("_") and k not in ("result", "table", "station_id", "network")})
                return result
        return None

    def query_latest_all_stations(self) -> dict[str, dict]:
        """
        Return the most-recent measurement for **every** station that has
        written data in the last 24 h.

        Returns a dict keyed by ``station_id``.
        """
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -24h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> last()
  |> pivot(rowKey: ["_time", "station_id", "network"], columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB query_latest_all error: %s", exc)
            return {}

        results: dict[str, dict] = {}
        for table in tables:
            for record in table.records:
                sid = record.values.get("station_id", "")
                fields = {
                    k: v
                    for k, v in record.values.items()
                    if not k.startswith("_") and k not in ("result", "table", "station_id", "network")
                }
                if sid not in results:
                    results[sid] = {
                        "station_id": sid,
                        "network": record.values.get("network", ""),
                        "timestamp": record.get_time(),
                    }
                # Merge fields — never overwrite an existing non-None value with None
                for k, v in fields.items():
                    if k not in results[sid] or (results[sid][k] is None and v is not None):
                        results[sid][k] = v
        return results

    # ------------------------------------------------------------------
    # Query — latest measurements for a specific set of stations (föhn)
    # ------------------------------------------------------------------

    def query_latest_for_stations(self, station_ids: list[str]) -> dict[str, dict]:
        """Return the most-recent measurement for each station in ``station_ids``.

        Returns a dict keyed by ``station_id``. Only stations that have written
        data in the last 2 hours are included.
        """
        if not station_ids:
            return {}
        ids_literal = '["' + '", "'.join(station_ids) + '"]'
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -2h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => contains(value: r.station_id, set: {ids_literal}))
  |> last()
  |> pivot(rowKey: ["_time", "station_id", "network"], columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB query_latest_for_stations error: %s", exc)
            return {}

        results: dict[str, dict] = {}
        for table in tables:
            for record in table.records:
                sid = record.values.get("station_id", "")
                if not sid:
                    continue
                entry: dict = {
                    "station_id": sid,
                    "timestamp": record.get_time(),
                }
                entry.update({
                    k: v for k, v in record.values.items()
                    if not k.startswith("_") and k not in ("result", "table", "station_id", "network")
                })
                results[sid] = entry
        return results

    def query_observation_snapshot_for_stations(self, station_ids: list[str], valid_time: datetime) -> dict[str, dict]:
        """Fetch observed weather for specific stations at ``valid_time``.

        Scans ±30 minutes around ``valid_time`` in ``weather_data`` and returns
        the last record per station (closest to the requested time).
        """
        if not station_ids:
            return {}
        ids_literal = '["' + '", "'.join(station_ids) + '"]'
        start = (valid_time - timedelta(minutes=30)).isoformat()
        stop  = (valid_time + timedelta(minutes=31)).isoformat()
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => contains(value: r.station_id, set: {ids_literal}))
  |> last()
  |> pivot(rowKey: ["_time", "station_id", "network"], columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB query_observation_snapshot_for_stations error: %s", exc)
            return {}

        results: dict[str, dict] = {}
        for table in tables:
            for record in table.records:
                sid = record.values.get("station_id", "")
                if not sid:
                    continue
                entry: dict = {
                    "station_id": sid,
                    "timestamp": record.get_time(),
                }
                entry.update({
                    k: v for k, v in record.values.items()
                    if not k.startswith("_") and k not in ("result", "table", "station_id", "network")
                })
                results[sid] = entry
        return results

    def query_forecast_snapshot_for_stations(self, station_ids: list[str], valid_time: datetime) -> dict[str, dict]:
        """Fetch the most recent forecast for specific stations at ``valid_time``.

        Scans ±30 minutes around ``valid_time`` in ``weather_forecast`` and returns
        the last written record per station (= newest init_time = freshest model run).
        Returns a dict keyed by ``station_id`` with the same field shape as
        ``query_latest_for_stations``.
        """
        if not station_ids:
            return {}
        ids_literal = '["' + '", "'.join(station_ids) + '"]'
        start = (valid_time - timedelta(minutes=30)).isoformat()
        stop  = (valid_time + timedelta(minutes=31)).isoformat()
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start}, stop: {stop})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_FORECAST}")
  |> filter(fn: (r) => contains(value: r.station_id, set: {ids_literal}))
  |> last()
  |> pivot(rowKey: ["_time", "station_id", "network", "model"],
           columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB query_forecast_snapshot_for_stations error: %s", exc)
            return {}

        results: dict[str, dict] = {}
        for table in tables:
            for record in table.records:
                sid = record.values.get("station_id", "")
                if not sid:
                    continue
                entry: dict = {
                    "station_id":  sid,
                    "timestamp":   record.get_time(),
                    "is_forecast": True,
                }
                entry.update({
                    k: v for k, v in record.values.items()
                    if not k.startswith("_")
                    and k not in ("result", "table", "station_id", "network", "model", "init_time")
                })
                # Keep the entry with the most fields (newest init_time written last)
                if sid not in results or len(entry) > len(results[sid]):
                    results[sid] = entry
        return results

    def query_foehn_pressure_history(
        self, station_ids: list[str], hours: int = 48, center_time: Optional[datetime] = None
    ) -> list[dict]:
        """Return hourly-averaged ``pressure_qff`` per station for a ±(hours/2) window.

        ``center_time=None`` → live mode: show the last ``hours`` hours ending now.
        ``center_time`` set   → show ``hours/2`` before and after that moment,
                                using observed data for the past half and forecast
                                data (``weather_forecast``) for any future half.
        Each row: ``{station_id, timestamp, pressure_qff}``.
        """
        if not station_ids:
            return []
        ids_literal = '["' + '", "'.join(station_ids) + '"]'
        now = datetime.now(timezone.utc)
        half = hours // 2

        if center_time is None:
            obs_start = now - timedelta(hours=hours)
            obs_stop  = now
            fc_start  = None
            fc_stop   = None
        else:
            window_start = center_time - timedelta(hours=half)
            window_stop  = center_time + timedelta(hours=half)
            # Observations cover the past portion of the window
            obs_start = window_start
            obs_stop  = min(window_stop, now)
            # Forecasts cover the future portion of the window (if any)
            if window_stop > now:
                fc_start = max(window_start, now)
                fc_stop  = window_stop
            else:
                fc_start = None
                fc_stop  = None

        def _flux_pressure(measurement: str, start: datetime, stop: datetime) -> str:
            s = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            e = stop.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            return f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {s}, stop: {e})
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => contains(value: r.station_id, set: {ids_literal}))
  |> filter(fn: (r) => r._field == "pressure_qff")
  |> aggregateWindow(every: 1h, fn: mean, createEmpty: false)
  |> sort(columns: ["_time"])
"""

        rows: list[dict] = []

        def _run(flux: str, label: str) -> None:
            try:
                for table in self._query_api.query(flux, org=self._cfg.org):
                    for record in table.records:
                        sid = record.values.get("station_id", "")
                        val = record.get_value()
                        if sid and val is not None:
                            rows.append({
                                "station_id":   sid,
                                "timestamp":    record.get_time(),
                                "pressure_qff": val,
                            })
            except Exception as exc:
                logger.error("InfluxDB query_foehn_pressure_history %s error: %s", label, exc)

        if obs_start < obs_stop:
            _run(_flux_pressure(MEASUREMENT_WEATHER, obs_start, obs_stop), "obs")
        if fc_start:
            _run(_flux_pressure(MEASUREMENT_FORECAST, fc_start, fc_stop), "fc")

        return sorted(rows, key=lambda r: r["timestamp"])

    # ------------------------------------------------------------------
    # Query — latest / history for virtual (multi-member) stations
    # ------------------------------------------------------------------

    def query_latest_virtual(self, member_ids: list[str]) -> Optional[dict]:
        """Return the most-recent measurement across all ``member_ids``.

        Queries all members in a single round-trip and returns the entry
        with the most recent timestamp, implementing "newest-wins" for
        virtual (proximity-deduplicated) stations.
        """
        if not member_ids:
            return None
        if len(member_ids) == 1:
            return self.query_latest(member_ids[0])
        results = self.query_latest_for_stations(member_ids)
        if not results:
            return None
        return max(results.values(), key=lambda r: r.get("timestamp", datetime.min.replace(tzinfo=timezone.utc)))

    def _members_established_for_window(self, station_ids: list[str], hours: int) -> list[str]:
        """Return the subset of ``station_ids`` that were already delivering data
        BEFORE the start of the requested history window.

        Checks a 2-hour slice just before the window opens
        (range: -(hours+2)h → -hours h).  A station with data in that slice was
        running before the window started and is considered established — it
        produces a continuous time-series across the full window.

        A newly-added station that only has data WITHIN the window is excluded,
        preventing partial-coverage rows from mixing with an established station's
        full history (which makes the chart look jagged/duplicated).

        Falls back to any station with data anywhere in the window when no
        station passes the pre-window check, so history never silently disappears.
        Falls back further to all IDs on query error.
        """
        if not station_ids:
            return []
        ids_literal = '["' + '", "'.join(station_ids) + '"]'
        # Lightweight check: last() + pivot gives one row per station that has
        # data in the pre-window slice — same pattern as query_latest_for_stations.
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{hours + 2}h, stop: -{hours}h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => contains(value: r.station_id, set: {ids_literal}))
  |> last()
  |> pivot(rowKey: ["_time", "station_id", "network"], columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
            established = {
                record.values.get("station_id", "")
                for table in tables
                for record in table.records
                if record.values.get("station_id")
            }
            result = [sid for sid in station_ids if sid in established]
            if result:
                return result

            # No member predates the window (all are newly added).
            # Fall back to members that have any data within the window.
            in_window_flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => contains(value: r.station_id, set: {ids_literal}))
  |> last()
  |> pivot(rowKey: ["_time", "station_id", "network"], columnKey: ["_field"], valueColumn: "_value")
"""
            tables2 = self._query_api.query(in_window_flux, org=self._cfg.org)
            in_window = {
                record.values.get("station_id", "")
                for table in tables2
                for record in table.records
                if record.values.get("station_id")
            }
            return [sid for sid in station_ids if sid in in_window]

        except Exception as exc:
            logger.warning("InfluxDB _members_established_for_window fallback for %s: %s", station_ids, exc)
            return station_ids  # safe fallback: include all

    def query_history_virtual(self, member_ids: list[str], hours: int = 24) -> list[dict]:
        """Return pooled time-series data for all ``member_ids`` over the last ``hours`` hours.

        Only members that were already delivering data before the window started
        are included.  This prevents newly-added stations (which only have partial
        coverage) from producing jagged or duplicated chart lines alongside an
        established station's full history.

        If a member stops delivering data, it is automatically excluded from
        subsequent history windows once its data predates the window.

        Rows from different qualifying members are pooled, producing a denser
        time-series than a single station would provide.  ``station_id`` is
        stripped from output rows (consistent with ``query_history``).
        """
        if not member_ids:
            return []

        active = self._members_established_for_window(member_ids, hours)
        if not active:
            return []
        if len(active) == 1:
            return self.query_history(active[0], hours)

        ids_literal = '["' + '", "'.join(active) + '"]'
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => contains(value: r.station_id, set: {ids_literal}))
  |> pivot(rowKey: ["_time", "station_id"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB query_history_virtual error for %s: %s", active, exc)
            return []

        rows: list[dict] = []
        for table in tables:
            for record in table.records:
                entry: dict = {"timestamp": record.get_time()}
                entry.update({
                    k: v for k, v in record.values.items()
                    if not k.startswith("_") and k not in ("result", "table", "station_id", "network")
                })
                rows.append(entry)
        return rows

    # ------------------------------------------------------------------
    # Query — history (last N hours)
    # ------------------------------------------------------------------

    def query_history(self, station_id: str, hours: int = 24) -> list[dict]:
        """
        Return time-series data for ``station_id`` over the last ``hours`` hours.
        Each entry is a dict with ``timestamp`` plus all available field values.
        """
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => r.station_id == "{station_id}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB history query error for %s: %s", station_id, exc)
            return []

        rows: list[dict] = []
        for table in tables:
            for record in table.records:
                entry: dict = {"timestamp": record.get_time()}
                entry.update({k: v for k, v in record.values.items() if not k.startswith("_") and k not in ("result", "table", "station_id", "network")})
                rows.append(entry)
        return rows

    # ------------------------------------------------------------------
    # Query — data bounds (earliest / latest timestamp with any data)
    # ------------------------------------------------------------------

    def query_data_bounds(self) -> dict:
        """
        Return the earliest and latest timestamps that have any recorded data.
        Scans up to 365 days back for the earliest point.
        """
        flux_earliest = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -365d)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> keep(columns: ["_time"])
  |> sort(columns: ["_time"])
  |> first()
"""
        flux_latest = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -48h)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> keep(columns: ["_time"])
  |> last()
"""
        earliest: Optional[datetime] = None
        latest: Optional[datetime] = None

        try:
            for table in self._query_api.query(flux_earliest, org=self._cfg.org):
                for record in table.records:
                    t = record.get_time()
                    if earliest is None or t < earliest:
                        earliest = t
        except Exception as exc:
            logger.error("InfluxDB data_bounds earliest error: %s", exc)

        try:
            for table in self._query_api.query(flux_latest, org=self._cfg.org):
                for record in table.records:
                    t = record.get_time()
                    if latest is None or t > latest:
                        latest = t
        except Exception as exc:
            logger.error("InfluxDB data_bounds latest error: %s", exc)

        return {"earliest": earliest, "latest": latest}

    # ------------------------------------------------------------------
    # Query — history for all stations (replay)
    # ------------------------------------------------------------------

    def query_history_all_stations(self, start: datetime, end: datetime) -> dict[str, list[dict]]:
        """
        Return time-series data for **every** station between ``start`` and ``end``.
        Returns a dict keyed by ``station_id``, each value a time-sorted list of measurement dicts.
        """
        start_str = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        # aggregateWindow(30m) reduces ~144 raw rows/station/day to ~48 before pivot,
        # cutting pivot memory and CPU by ~3× with no visible loss for day-view replay.
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> aggregateWindow(every: 30m, fn: last, createEmpty: false)
  |> pivot(rowKey: ["_time", "station_id", "network"], columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB history_all query error: %s", exc)
            return {}

        results: dict[str, list[dict]] = {}
        for table in tables:
            for record in table.records:
                sid = record.values.get("station_id", "")
                entry: dict = {"timestamp": record.get_time()}
                entry.update({
                    k: v for k, v in record.values.items()
                    if not k.startswith("_") and k not in ("result", "table", "station_id", "network")
                })
                results.setdefault(sid, []).append(entry)
        return results

    # ------------------------------------------------------------------
    # Idempotency guard
    # ------------------------------------------------------------------

    def has_measure(self, station_id: str, timestamp: datetime) -> bool:
        """
        Return ``True`` if a measurement for ``station_id`` at ``timestamp``
        already exists in InfluxDB.

        Used by collectors to avoid duplicate writes on re-runs.
        """
        # Convert to RFC3339 string for Flux
        ts_str = timestamp.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {ts_str}, stop: {ts_str})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => r.station_id == "{station_id}")
  |> count()
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
            for table in tables:
                for record in table.records:
                    return (record.get_value() or 0) > 0
        except Exception as exc:
            logger.warning("has_measure check failed for %s @ %s: %s", station_id, ts_str, exc)
        return False

    # ------------------------------------------------------------------
    # Query — ruleset decision history
    # ------------------------------------------------------------------

    def query_decision_history(self, ruleset_id: str, hours: int = 24) -> list[dict]:
        """
        Return historical evaluation decisions for ``ruleset_id`` over the
        last ``hours`` hours, sorted chronologically.
        """
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "rule_decisions")
  |> filter(fn: (r) => r.ruleset_id == "{ruleset_id}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB decision_history query error for %s: %s", ruleset_id, exc)
            return []

        rows: list[dict] = []
        for table in tables:
            for record in table.records:
                rows.append({
                    "timestamp": record.get_time().isoformat(),
                    "decision": record.values.get("decision"),
                    "condition_results_json": record.values.get("condition_results"),
                })
        return rows

    def query_extremes_for_period(
        self,
        start: datetime,
        end: datetime,
        measurement: str = MEASUREMENT_WEATHER,
    ) -> tuple[list[dict], list[dict]]:
        """
        Return per-(station, field) max and min records for a time range.

        Uses Flux ``max()`` / ``min()`` aggregations so only aggregate rows
        are returned — not the full raw time-series.

        Returns ``(max_records, min_records)`` where each is a list of::

            {station_id, network, field, value, timestamp}
        """
        _FIELDS = [
            "wind_speed", "wind_gust", "temperature",
            "pressure_qnh", "humidity", "precipitation", "snow_depth",
        ]
        field_filter = " or ".join(f'r._field == "{f}"' for f in _FIELDS)
        start_str = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        def _run(agg_fn: str) -> list[dict]:
            flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r._measurement == "{measurement}")
  |> filter(fn: (r) => {field_filter})
  |> {agg_fn}()
"""
            records: list[dict] = []
            try:
                for table in self._query_api.query(flux, org=self._cfg.org):
                    for record in table.records:
                        val = record.get_value()
                        if val is None:
                            continue
                        records.append({
                            "station_id": record.values.get("station_id", ""),
                            "network": record.values.get("network", ""),
                            "field": record.get_field(),
                            "value": float(val),
                            "timestamp": record.get_time().isoformat() if record.get_time() else None,
                        })
            except Exception as exc:
                logger.error("InfluxDB %s extremes query error (%s): %s", agg_fn, measurement, exc)
            return records

        return _run("max"), _run("min")

    def query_decision_history_multi(
        self, ruleset_ids: list[str], hours: int = 24
    ) -> dict[str, list[dict]]:
        """
        Return decision history for multiple rulesets in one Flux query.

        Returns ``{ruleset_id: [{"timestamp", "decision", "condition_results_json"}, …]}``.
        """
        if not ruleset_ids:
            return {}

        id_filter = " or ".join(f'r.ruleset_id == "{rid}"' for rid in ruleset_ids)
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{hours}h)
  |> filter(fn: (r) => r._measurement == "rule_decisions")
  |> filter(fn: (r) => {id_filter})
  |> pivot(rowKey: ["_time", "ruleset_id"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB decision_history_multi error: %s", exc)
            return {}

        results: dict[str, list[dict]] = {}
        for table in tables:
            for record in table.records:
                rid = record.values.get("ruleset_id", "")
                results.setdefault(rid, []).append({
                    "timestamp": record.get_time().isoformat(),
                    "decision": record.values.get("decision"),
                    "condition_results_json": record.values.get("condition_results"),
                })
        return results

    # ------------------------------------------------------------------
    # Forecast — write
    # ------------------------------------------------------------------

    def write_forecast(self, points: list[ForecastPoint]) -> None:
        """
        Write a batch of ForecastPoint objects to the ``weather_forecast`` measurement.

        Tags:   ``station_id``, ``network``, ``source``, ``model``, ``init_date``
        Time:   ``valid_time`` (the future moment the forecast applies to)
        Fields: weather fields + ``init_time`` (ISO string) for deduplication

        ``init_date`` (YYYY-MM-DD UTC) is a tag so that each calendar day of model
        runs forms its own InfluxDB series.  Within a day, later runs overwrite
        earlier ones (same tags + timestamp), but runs from different days are kept
        independently — enabling per-day forecast accuracy comparisons.
        """
        if not points:
            return

        influx_points: list[Point] = []
        for fp in points:
            p = (
                Point(MEASUREMENT_FORECAST)
                .tag("station_id", fp.station_id)
                .tag("network", fp.network)
                .tag("source", fp.source)
                .tag("model", fp.model)
                .tag("init_date", fp.init_time.astimezone(timezone.utc).strftime("%Y-%m-%d"))
                .time(int(fp.valid_time.timestamp()), "s")
            )
            field_map = {
                "wind_speed":     fp.wind_speed,
                "wind_gust":      fp.wind_gust,
                "wind_direction": fp.wind_direction,
                "temperature":    fp.temperature,
                "humidity":       fp.humidity,
                "pressure_qnh":   fp.pressure_qnh,
                "precipitation":  fp.precipitation,
            }
            for field_name, value in field_map.items():
                if value is not None:
                    p = p.field(field_name, float(value))
            # Store init_time as a field so Python can deduplicate per valid_time
            p = p.field("init_time", fp.init_time.astimezone(timezone.utc).isoformat())
            influx_points.append(p)

        try:
            self._write_api.write(
                bucket=self._cfg.bucket, org=self._cfg.org, record=influx_points
            )
            logger.debug("Wrote %d forecast points to InfluxDB", len(influx_points))
        except Exception as exc:
            logger.error("InfluxDB forecast write error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Wind forecast grid — write + query
    # ------------------------------------------------------------------

    def write_forecast_grid(self, points: list) -> None:
        """
        Write a batch of GridForecastPoint objects to the ``wind_forecast_grid``
        measurement.

        Tags:   ``grid_id``, ``level_hpa``, ``init_date``
        Fields: ``wind_speed``, ``wind_direction``, ``lat``, ``lon``
        Time:   ``valid_time`` (UTC)
        """
        from lenticularis.models.weather import GridForecastPoint  # avoid circular at module level
        if not points:
            return

        influx_points: list[Point] = []
        for fp in points:
            p = (
                Point("wind_forecast_grid")
                .tag("grid_id",   fp.grid_id)
                .tag("level_hpa", str(fp.level_hpa))
                .tag("init_date", fp.init_time.astimezone(timezone.utc).strftime("%Y-%m-%d"))
                .time(int(fp.valid_time.timestamp()), "s")
                .field("lat", float(fp.lat))
                .field("lon", float(fp.lon))
            )
            if fp.wind_speed is not None:
                p = p.field("wind_speed", float(fp.wind_speed))
            if fp.wind_direction is not None:
                p = p.field("wind_direction", float(fp.wind_direction))
            influx_points.append(p)

        try:
            self._write_api.write(
                bucket=self._cfg.bucket, org=self._cfg.org, record=influx_points
            )
            logger.debug("Wrote %d wind_forecast_grid points to InfluxDB", len(influx_points))
        except Exception as exc:
            logger.error("InfluxDB wind_forecast_grid write error: %s", exc)
            raise

    def query_forecast_grid(
        self, start_dt: datetime, end_dt: datetime, level_hpa: int
    ) -> list[dict]:
        """
        Return all grid-cell wind forecasts for the given day and pressure level.

        Deduplicates to the latest ``init_date`` per (``grid_id``, ``valid_time``).

        Returns a flat list of dicts:
            {"grid_id": str, "lat": float, "lon": float,
             "valid_time": ISO str, "wind_speed": float|None,
             "wind_direction": int|None}
        """
        start_str = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str   = end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r._measurement == "wind_forecast_grid")
  |> filter(fn: (r) => r.level_hpa == "{level_hpa}")
  |> pivot(rowKey: ["_time", "grid_id", "level_hpa", "init_date"], columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB query_forecast_grid error: %s", exc)
            return []

        # Deduplicate: keep latest init_date per (grid_id, valid_time)
        raw: dict[tuple[str, str], dict] = {}
        for table in tables:
            for record in table.records:
                grid_id    = record.values.get("grid_id", "")
                vt_iso     = record.get_time().isoformat()
                init_date  = record.values.get("init_date", "")
                key = (grid_id, vt_iso)
                existing = raw.get(key)
                if existing is None or init_date > existing.get("_init_date", ""):
                    raw[key] = {
                        "grid_id":        grid_id,
                        "lat":            record.values.get("lat"),
                        "lon":            record.values.get("lon"),
                        "valid_time":     vt_iso,
                        "wind_speed":     record.values.get("wind_speed"),
                        "wind_direction": record.values.get("wind_direction"),
                        "_init_date":     init_date,
                    }

        result = []
        for row in raw.values():
            wd = row.get("wind_direction")
            result.append({
                "grid_id":        row["grid_id"],
                "lat":            row["lat"],
                "lon":            row["lon"],
                "valid_time":     row["valid_time"],
                "wind_speed":     row.get("wind_speed"),
                "wind_direction": int(wd) if wd is not None else None,
            })
        result.sort(key=lambda r: r["valid_time"])
        logger.debug(
            "query_forecast_grid: %d rows for level_hpa=%d, %s–%s",
            len(result), level_hpa, start_str, end_str,
        )
        return result

    # ------------------------------------------------------------------
    # Forecast — query
    # ------------------------------------------------------------------

    def query_forecast_replay(
        self, start_dt: datetime, end_dt: datetime
    ) -> dict[str, list[dict]]:
        """
        Return forecast data for **all** stations between ``start_dt`` and ``end_dt``,
        in the same shape as ``query_history_all_stations``:

            { station_id: [ {"timestamp": ISO_str, field: value, ...}, ... ] }

        Deduplicates to the latest ``init_time`` per ``valid_time`` per station.
        Timestamps are returned as ISO strings (matching the observation replay format).
        """
        start_str = start_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = end_dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_FORECAST}")
  |> pivot(rowKey: ["_time", "station_id", "network", "source", "model"], columnKey: ["_field"], valueColumn: "_value")
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB forecast_replay query error: %s", exc)
            return {}

        # raw[station_id][valid_time_iso] = {fields..., "_init_time": str}
        raw: dict[str, dict[str, dict]] = {}
        for table in tables:
            for record in table.records:
                sid = record.values.get("station_id", "")
                vt_iso = record.get_time().isoformat()
                init_time_str = record.values.get("init_time", "")

                fields = {
                    k: v
                    for k, v in record.values.items()
                    if not k.startswith("_")
                    and k not in ("result", "table", "station_id", "network", "source", "model", "init_time", "init_date")
                }
                fields["_init_time"] = init_time_str

                raw.setdefault(sid, {})
                existing = raw[sid].get(vt_iso)
                if existing is None or init_time_str > existing.get("_init_time", ""):
                    raw[sid][vt_iso] = fields

        # Convert to sorted measurement lists, stripping internal key
        result: dict[str, list[dict]] = {}
        for sid, by_vt in raw.items():
            measurements = [
                {"timestamp": vt_iso, **{k: v for k, v in row.items() if k != "_init_time"}}
                for vt_iso, row in sorted(by_vt.items())
            ]
            result[sid] = measurements

        return result

    def query_forecast_for_stations(
        self, station_ids: list[str], horizon_hours: int = 120
    ) -> dict[str, dict[str, dict]]:
        """
        Return forecast data for the given station IDs from now to ``+horizon_hours``.

        For each station, deduplicates to the latest ``init_time`` per ``valid_time``
        in Python (all model runs are stored; we pick the most recent one for evaluation).

        Returns::

            {
                station_id: {
                    valid_time_iso: {
                        "wind_speed": float | None,
                        "wind_gust":  float | None,
                        ...
                    }
                }
            }
        """
        if not station_ids:
            return {}

        now = datetime.now(timezone.utc)
        start_str = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str = (now + __import__("datetime").timedelta(hours=horizon_hours)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )

        station_filter = " or ".join(
            f'r.station_id == "{sid}"' for sid in station_ids
        )
        # Limit to runs from the last 3 days so we don't pull all historical init_dates.
        # Old-format data (no init_date tag) is included via the `not exists` branch.
        three_days_ago = (datetime.now(timezone.utc) - timedelta(days=3)).strftime("%Y-%m-%d")

        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_FORECAST}")
  |> filter(fn: (r) => {station_filter})
  |> filter(fn: (r) => not exists r.init_date or r.init_date >= "{three_days_ago}")
  |> pivot(rowKey: ["_time", "station_id", "network", "source", "model", "init_date"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB forecast query error: %s", exc)
            return {}

        # raw[station_id][valid_time_iso] = {fields..., "_init_time": str}
        raw: dict[str, dict[str, dict]] = {}
        for table in tables:
            for record in table.records:
                sid = record.values.get("station_id", "")
                valid_time_iso = record.get_time().isoformat()
                init_time_str = record.values.get("init_time", "")

                fields = {
                    k: v
                    for k, v in record.values.items()
                    if not k.startswith("_")
                    and k not in ("result", "table", "station_id", "network", "source", "model", "init_time", "init_date")
                }
                fields["_init_time"] = init_time_str

                raw.setdefault(sid, {})
                existing = raw[sid].get(valid_time_iso)
                # Keep row with the latest init_time (lexicographic compare of ISO strings)
                if existing is None or init_time_str > existing.get("_init_time", ""):
                    raw[sid][valid_time_iso] = fields

        # Strip internal tracking key
        return {
            sid: {vt: {k: v for k, v in row.items() if k != "_init_time"} for vt, row in by_vt.items()}
            for sid, by_vt in raw.items()
        }

    def query_history_for_stations(
        self, station_ids: list[str], days: int = 30, window_minutes: int = 30
    ) -> dict[str, dict[str, dict]]:
        """
        Return historical ``weather_data`` for the given stations over the last
        ``days`` days, aggregated into ``window_minutes``-minute buckets.

        Returns the same shape as ``query_forecast_for_stations``::

            {
                station_id: {
                    timestamp_iso: {
                        "wind_speed": float | None,
                        ...
                    }
                }
            }

        Each bucket uses ``last()`` aggregation — the most recent measurement
        within the window — matching the replay-cache strategy.
        """
        if not station_ids:
            return {}

        station_filter = " or ".join(
            f'r.station_id == "{sid}"' for sid in station_ids
        )
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => {station_filter})
  |> aggregateWindow(every: {window_minutes}m, fn: last, createEmpty: false)
  |> pivot(rowKey: ["_time", "station_id", "network"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
        except Exception as exc:
            logger.error("InfluxDB query_history_for_stations error: %s", exc)
            return {}

        results: dict[str, dict[str, dict]] = {}
        for table in tables:
            for record in table.records:
                sid = record.values.get("station_id", "")
                if not sid:
                    continue
                vt_iso = record.get_time().isoformat()
                fields = {
                    k: v
                    for k, v in record.values.items()
                    if not k.startswith("_")
                    and k not in ("result", "table", "station_id", "network")
                    and v is not None
                }
                results.setdefault(sid, {})[vt_iso] = fields
        return results

    def query_forecast_accuracy(
        self, station_id: str, start: datetime, end: datetime
    ) -> dict:
        """
        Return actual observations and per-init_date model forecasts for the window.

        New-format data (written after the ``init_date`` tag was added to
        ``write_forecast``) is grouped by init_date so each calendar day of model
        runs is a separate series.  Legacy data (no ``init_date`` tag) is returned
        as a single fallback series.

        Returns::

            {
                "actual":   [{"timestamp": ISO, "wind_speed": float, …}, …],
                "forecasts": [
                    {"init_date": "2025-03-21", "data": [{"timestamp": ISO, …}, …]},
                    …  (sorted newest first; legacy entries have init_date=None)
                ],
            }
        """
        _WEATHER_FIELDS = {
            "wind_speed", "wind_gust", "wind_direction",
            "temperature", "humidity", "pressure_qnh", "precipitation", "snow_depth",
        }
        start_str = start.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
        end_str   = end.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

        # -- Actual observations --------------------------------------------------
        flux_actual = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_WEATHER}")
  |> filter(fn: (r) => r.station_id == "{station_id}")
  |> pivot(rowKey: ["_time"], columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        actual: list[dict] = []
        try:
            for table in self._query_api.query(flux_actual, org=self._cfg.org):
                for record in table.records:
                    entry: dict = {"timestamp": record.get_time().isoformat()}
                    entry.update({
                        k: v for k, v in record.values.items()
                        if k in _WEATHER_FIELDS and v is not None
                    })
                    actual.append(entry)
        except Exception as exc:
            logger.error("query_forecast_accuracy actual error for %s: %s", station_id, exc)

        # -- New-format forecasts (grouped by init_date tag) ----------------------
        flux_fc_new = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_FORECAST}")
  |> filter(fn: (r) => r.station_id == "{station_id}")
  |> filter(fn: (r) => exists r.init_date)
  |> pivot(rowKey: ["_time", "station_id", "network", "source", "model", "init_date"],
           columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        # raw_new[init_date][valid_time_iso] = fields — last written run of that day wins
        raw_new: dict[str, dict[str, dict]] = {}
        try:
            for table in self._query_api.query(flux_fc_new, org=self._cfg.org):
                for record in table.records:
                    init_date = record.values.get("init_date", "") or ""
                    if not init_date:
                        continue
                    vt_iso = record.get_time().isoformat()
                    fields: dict = {"timestamp": vt_iso}
                    fields.update({
                        k: v for k, v in record.values.items()
                        if k in _WEATHER_FIELDS and v is not None
                    })
                    raw_new.setdefault(init_date, {})[vt_iso] = fields
        except Exception as exc:
            logger.error("query_forecast_accuracy new-format error for %s: %s", station_id, exc)

        # -- Legacy forecasts (no init_date tag — old data written before schema change) --
        flux_fc_legacy = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: {start_str}, stop: {end_str})
  |> filter(fn: (r) => r._measurement == "{MEASUREMENT_FORECAST}")
  |> filter(fn: (r) => r.station_id == "{station_id}")
  |> filter(fn: (r) => not exists r.init_date)
  |> pivot(rowKey: ["_time", "station_id", "network", "source", "model"],
           columnKey: ["_field"], valueColumn: "_value")
  |> sort(columns: ["_time"])
"""
        raw_legacy: dict[str, dict] = {}
        try:
            for table in self._query_api.query(flux_fc_legacy, org=self._cfg.org):
                for record in table.records:
                    vt_iso = record.get_time().isoformat()
                    fields_l: dict = {"timestamp": vt_iso}
                    fields_l.update({
                        k: v for k, v in record.values.items()
                        if k in _WEATHER_FIELDS and v is not None
                    })
                    existing = raw_legacy.get(vt_iso)
                    if existing is None or len(fields_l) > len(existing):
                        raw_legacy[vt_iso] = fields_l
        except Exception as exc:
            logger.error("query_forecast_accuracy legacy error for %s: %s", station_id, exc)

        # -- Assemble forecasts list (newest init_date first) ---------------------
        forecasts: list[dict] = []
        for init_date in sorted(raw_new.keys(), reverse=True):
            data = [row for _, row in sorted(raw_new[init_date].items())]
            if data:
                forecasts.append({"init_date": init_date, "data": data})
        if raw_legacy:
            legacy_data = [row for _, row in sorted(raw_legacy.items())]
            forecasts.append({"init_date": None, "data": legacy_data})

        return {"actual": actual, "forecasts": forecasts}

    # ------------------------------------------------------------------
    # Storage / ingestion stats
    # ------------------------------------------------------------------

    def query_measurement_count(self, measurement: str, days: int = 365) -> int:
        """
        Return the approximate number of records in ``measurement`` over the
        last ``days`` days.  Counts the ``wind_speed`` field as a proxy
        (present in virtually all weather and forecast rows).
        """
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "wind_speed")
  |> group()
  |> count()
"""
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
            for table in tables:
                for record in table.records:
                    return int(record.get_value() or 0)
        except Exception as exc:
            logger.error("InfluxDB count error (%s): %s", measurement, exc)
        return 0

    def query_daily_ingestion(self, measurement: str, days: int = 30) -> list[dict]:
        """
        Return daily record counts for ``measurement`` over the last ``days`` days.

        Returns ``[{"date": "YYYY-MM-DD", "count": int}, ...]`` sorted chronologically.
        """
        flux = f"""
from(bucket: "{self._cfg.bucket}")
  |> range(start: -{days}d)
  |> filter(fn: (r) => r._measurement == "{measurement}" and r._field == "wind_speed")
  |> group()
  |> aggregateWindow(every: 1d, fn: count, createEmpty: true)
"""
        result = []
        try:
            tables = self._query_api.query(flux, org=self._cfg.org)
            for table in tables:
                for record in table.records:
                    ts = record.get_time()
                    if ts is None:
                        continue
                    result.append({
                        "date": ts.strftime("%Y-%m-%d"),
                        "count": int(record.get_value() or 0),
                    })
        except Exception as exc:
            logger.error("InfluxDB daily ingestion error (%s): %s", measurement, exc)
        return sorted(result, key=lambda x: x["date"])

    def query_storage_bytes(self) -> int | None:
        """
        Return the total on-disk storage used by InfluxDB (all shards) in bytes,
        by scraping the Prometheus ``/metrics`` endpoint.

        InfluxDB 2.x labels shards with a ``bucket_id`` UUID, not the bucket name,
        so we sum *all* ``storage_shard_disk_size_bytes`` lines — accurate for a
        dedicated Lenticularis InfluxDB instance.

        Tries with the configured token first; falls back to no auth (some setups
        expose ``/metrics`` without authentication for Prometheus scraping).

        Returns ``None`` when the endpoint is unreachable or the metric is absent.
        """
        import urllib.request
        import urllib.error

        url = self._cfg.url.rstrip("/") + "/metrics"
        # InfluxDB 2.x uses this metric name; some builds prefix it with influxdb_
        _METRIC_NAMES = (
            "storage_shard_disk_size_bytes{",
            "influxdb_storage_shard_disk_size_bytes{",
        )

        def _fetch(auth: bool) -> str | None:
            headers = {"Authorization": f"Token {self._cfg.token}"} if auth else {}
            req = urllib.request.Request(url, headers=headers)
            try:
                with urllib.request.urlopen(req, timeout=5) as resp:
                    return resp.read().decode("utf-8")
            except urllib.error.HTTPError as exc:
                if exc.code in (401, 403) and auth:
                    return None  # signal to retry without auth
                logger.warning("InfluxDB /metrics HTTP %s", exc.code)
                return ""
            except Exception as exc:
                logger.warning("Could not fetch InfluxDB /metrics: %s", exc)
                return ""

        body = _fetch(auth=True)
        if body is None:           # 401/403 — retry without auth
            body = _fetch(auth=False)
        if not body:
            return None

        total = 0
        found = False
        for line in body.splitlines():
            if not any(line.startswith(m) for m in _METRIC_NAMES):
                continue
            try:
                value = float(line.split("}")[-1].strip())
                total += int(value)
                found = True
            except (ValueError, IndexError):
                continue
        return total if found else None

    # ------------------------------------------------------------------
    # Föhn status — write & query
    # ------------------------------------------------------------------

    def write_foehn_status(self, regions: list[dict]) -> None:
        """Write evaluated föhn region statuses to ``weather_data`` as virtual stations.

        Each region becomes one Point with station_id ``foehn-<key>`` and
        field ``foehn_active`` (1.0=active, 0.5=partial, 0.0=inactive, -1.0=no_data).

        An ``foehn-overall`` point is also written with the aggregate status.
        These virtual stations behave like real stations in the rule evaluator —
        pilots select them by name and use field ``foehn_active`` in conditions.
        """
        from lenticularis.foehn_detection import STATUS_TO_NUMERIC

        now_ts = int(datetime.now(timezone.utc).timestamp())
        points: list[Point] = []

        for r in regions:
            active_val = STATUS_TO_NUMERIC.get(r["status"], -1.0)
            points.append(
                Point(MEASUREMENT_WEATHER)
                .tag("station_id", f"foehn-{r['key']}")
                .tag("network", "foehn")
                .field("foehn_active", active_val)
                .time(now_ts, "s")
            )

        # Overall point
        if any(r["status"] == "active" for r in regions):
            overall_val = 1.0
        elif any(r["status"] == "partial" for r in regions):
            overall_val = 0.5
        else:
            overall_val = 0.0
        points.append(
            Point(MEASUREMENT_WEATHER)
            .tag("station_id", "foehn-overall")
            .tag("network", "foehn")
            .field("foehn_active", overall_val)
            .time(now_ts, "s")
        )

        try:
            self._write_api.write(bucket=self._cfg.bucket, org=self._cfg.org, record=points)
            logger.debug("Wrote %d foehn virtual-station points", len(points))
        except Exception as exc:
            logger.error("InfluxDB write_foehn_status error: %s", exc)
            raise

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying InfluxDB client."""
        self._client.close()
        logger.info("InfluxDB client closed")
