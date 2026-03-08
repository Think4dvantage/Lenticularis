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
from datetime import datetime, timezone
from typing import Optional

from influxdb_client import InfluxDBClient, Point
from influxdb_client.client.write_api import SYNCHRONOUS

from lenticularis.config import InfluxDBConfig
from lenticularis.models.weather import WeatherMeasurement

logger = logging.getLogger(__name__)

# InfluxDB measurement names
MEASUREMENT_WEATHER = "weather_data"


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
                entry: dict = {
                    "station_id": sid,
                    "network": record.values.get("network", ""),
                    "timestamp": record.get_time(),
                }
                entry.update({
                    k: v
                    for k, v in record.values.items()
                    if not k.startswith("_") and k not in ("result", "table", "station_id", "network")
                })
                results[sid] = entry
        return results

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
    # Lifecycle
    # ------------------------------------------------------------------

    def close(self) -> None:
        """Close the underlying InfluxDB client."""
        self._client.close()
        logger.info("InfluxDB client closed")
