"""
Weather statistics service.

Metrics derived from ``weather_data`` and ``weather_forecast`` InfluxDB
measurements.  All heavy computation is done in Python; Flux queries are kept
as simple range/filter/pivot operations.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from datetime import datetime, timedelta, timezone

from lenticularis.database.influx import (
    InfluxClient,
    MEASUREMENT_WEATHER,
    MEASUREMENT_FORECAST,
)

logger = logging.getLogger(__name__)

_STALE_THRESHOLD_MINUTES = 90


def _to_dt(ts) -> datetime:
    if isinstance(ts, str):
        return datetime.fromisoformat(ts)
    return ts


# ---------------------------------------------------------------------------
# Station freshness
# ---------------------------------------------------------------------------

def station_freshness(influx: InfluxClient) -> list[dict]:
    """
    Return freshness info for every station seen in the last 24 h.

    [{station_id, network, last_seen, age_minutes, is_stale}] sorted by age asc.
    """
    latest = influx.query_latest_all_stations()
    now = datetime.now(timezone.utc)
    result = []
    for sid, data in latest.items():
        ts = data.get("timestamp")
        if ts is None:
            continue
        ts = _to_dt(ts).astimezone(timezone.utc)
        age_minutes = round((now - ts).total_seconds() / 60, 1)
        result.append({
            "station_id": sid,
            "network": data.get("network", ""),
            "last_seen": ts.isoformat(),
            "age_minutes": age_minutes,
            "is_stale": age_minutes > _STALE_THRESHOLD_MINUTES,
        })
    result.sort(key=lambda x: x["age_minutes"])
    return result


# ---------------------------------------------------------------------------
# Network coverage
# ---------------------------------------------------------------------------

def network_coverage(influx: InfluxClient) -> list[dict]:
    """
    Return per-network totals: [{network, total, fresh, stale}] sorted by total desc.
    """
    latest = influx.query_latest_all_stations()
    now = datetime.now(timezone.utc)
    networks: dict[str, dict] = {}
    for sid, data in latest.items():
        net = data.get("network", "unknown")
        if net not in networks:
            networks[net] = {"network": net, "total": 0, "fresh": 0, "stale": 0}
        ts = data.get("timestamp")
        if ts is None:
            networks[net]["stale"] += 1
        else:
            age_min = (now - _to_dt(ts).astimezone(timezone.utc)).total_seconds() / 60
            if age_min <= _STALE_THRESHOLD_MINUTES:
                networks[net]["fresh"] += 1
            else:
                networks[net]["stale"] += 1
        networks[net]["total"] += 1
    return sorted(networks.values(), key=lambda x: x["total"], reverse=True)


# ---------------------------------------------------------------------------
# Wind rose — 16 directional bins
# ---------------------------------------------------------------------------

_DIRECTION_LABELS = [
    "N", "NNE", "NE", "ENE", "E", "ESE", "SE", "SSE",
    "S", "SSW", "SW", "WSW", "W", "WNW", "NW", "NNW",
]


def wind_rose(influx: InfluxClient, station_id: str, hours: int = 168) -> list[dict]:
    """
    Return 16-bin wind rose data for a station.

    [{direction_label, direction_center, count, avg_speed}]
    """
    rows = influx.query_history(station_id, hours)
    bins = [
        {"direction_label": d, "direction_center": i * 22.5, "count": 0, "speed_sum": 0.0}
        for i, d in enumerate(_DIRECTION_LABELS)
    ]
    for row in rows:
        wd = row.get("wind_direction")
        ws = row.get("wind_speed")
        if wd is None or ws is None:
            continue
        bin_idx = int((float(wd) + 11.25) / 22.5) % 16
        bins[bin_idx]["count"] += 1
        bins[bin_idx]["speed_sum"] += float(ws)

    return [
        {
            "direction_label": b["direction_label"],
            "direction_center": b["direction_center"],
            "count": b["count"],
            "avg_speed": round(b["speed_sum"] / b["count"], 1) if b["count"] else 0.0,
        }
        for b in bins
    ]


# ---------------------------------------------------------------------------
# Wind speed distribution — 2 km/h buckets
# ---------------------------------------------------------------------------

def wind_speed_distribution(influx: InfluxClient, station_id: str, hours: int = 168) -> list[dict]:
    """
    Return wind speed histogram in 2 km/h buckets.

    [{bucket_label, min, max, count}]
    """
    rows = influx.query_history(station_id, hours)
    buckets: dict[int, int] = defaultdict(int)
    for row in rows:
        ws = row.get("wind_speed")
        if ws is None:
            continue
        bucket = int(float(ws) / 2) * 2
        buckets[bucket] += 1

    if not buckets:
        return []

    max_bucket = max(buckets.keys())
    return [
        {
            "bucket_label": f"{b}–{b + 2}",
            "min": b,
            "max": b + 2,
            "count": buckets.get(b, 0),
        }
        for b in range(0, max_bucket + 2, 2)
    ]


# ---------------------------------------------------------------------------
# Temperature trend — daily min/mean/max
# ---------------------------------------------------------------------------

_FIELD_UNITS = {
    "wind_speed":    "km/h",
    "wind_gust":     "km/h",
    "temperature":   "°C",
    "pressure_qnh":  "hPa",
    "humidity":      "%",
    "precipitation": "mm",
    "snow_depth":    "cm",
}

# Which extremes to track per field (max-only or both)
_FIELD_EXTREMES = {
    "wind_speed":    ["max"],
    "wind_gust":     ["max"],
    "temperature":   ["max", "min"],
    "pressure_qnh":  ["max", "min"],
    "humidity":      ["max", "min"],
    "precipitation": ["max"],
    "snow_depth":    ["max"],
}


def _period_bounds(period: str) -> tuple[datetime, datetime]:
    """Return (start, end) UTC datetimes for the given period keyword."""
    now = datetime.now(timezone.utc)
    today_start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    if period == "today":
        return today_start, now
    if period == "yesterday":
        return today_start - timedelta(days=1), today_start
    if period == "last_week":
        return now - timedelta(days=7), now
    if period == "tomorrow":
        return now, now + timedelta(days=1)
    if period.startswith("date:"):
        d = datetime.strptime(period[5:], "%Y-%m-%d").replace(tzinfo=timezone.utc)
        return d, d + timedelta(days=1)
    raise ValueError(f"Unknown period: {period!r}")


def _build_extremes_from_records(
    max_records: list[dict],
    min_records: list[dict],
    station_registry: dict,
) -> dict:
    """Find the global max/min per field from per-(station, field) aggregate records."""
    result: dict[str, dict] = {f: {} for f in _FIELD_UNITS}

    for records, extreme_key in [(max_records, "max"), (min_records, "min")]:
        field_best: dict[str, dict] = {}
        for r in records:
            field = r["field"]
            if field not in _FIELD_UNITS:
                continue
            if extreme_key not in _FIELD_EXTREMES.get(field, []):
                continue
            val = r["value"]
            existing = field_best.get(field)
            if extreme_key == "max":
                better = existing is None or val > existing["value"]
            else:
                better = existing is None or val < existing["value"]
            if better:
                field_best[field] = r

        for field, r in field_best.items():
            sid = r["station_id"]
            station = station_registry.get(sid)
            result[field][extreme_key] = {
                "station_id": sid,
                "name": getattr(station, "name", None) or sid,
                "network": r["network"],
                "value": round(r["value"], 1),
                "unit": _FIELD_UNITS[field],
                "timestamp": r["timestamp"],
            }

    return result


def weather_extremes(
    influx: InfluxClient,
    station_registry: dict,
    period: str = "now",
) -> dict:
    """
    Return extreme (max and/or min) station per weather field.

    ``period`` values:
    - ``"now"``          — latest snapshot (fast, uses query_latest_all_stations)
    - ``"today"``        — UTC midnight → now
    - ``"yesterday"``    — yesterday UTC
    - ``"last_week"``    — rolling 7 days
    - ``"tomorrow"``     — next 24 h from forecast data
    - ``"date:YYYY-MM-DD"`` — specific date (UTC)
    """
    if period == "now":
        # Fast path: use the latest snapshot
        latest = influx.query_latest_all_stations()
        now = datetime.now(timezone.utc)
        result: dict[str, dict] = {f: {} for f in _FIELD_UNITS}
        for sid, data in latest.items():
            ts = data.get("timestamp")
            if ts is not None:
                age_min = (now - _to_dt(ts).astimezone(timezone.utc)).total_seconds() / 60
                if age_min > 180:
                    continue
            station = station_registry.get(sid)
            station_name = getattr(station, "name", None) or sid
            for field, extremes in _FIELD_EXTREMES.items():
                val = data.get(field)
                if val is None:
                    continue
                val = float(val)
                entry = {
                    "station_id": sid,
                    "name": station_name,
                    "network": data.get("network", ""),
                    "value": round(val, 1),
                    "unit": _FIELD_UNITS[field],
                    "timestamp": _to_dt(ts).isoformat() if ts else None,
                }
                for extreme in extremes:
                    existing = result[field].get(extreme)
                    if extreme == "max" and (existing is None or val > existing["value"]):
                        result[field]["max"] = entry
                    elif extreme == "min" and (existing is None or val < existing["value"]):
                        result[field]["min"] = entry
        return result

    # All other periods: use aggregation queries
    start, end = _period_bounds(period)
    measurement = MEASUREMENT_FORECAST if period == "tomorrow" else MEASUREMENT_WEATHER
    max_records, min_records = influx.query_extremes_for_period(start, end, measurement)
    return _build_extremes_from_records(max_records, min_records, station_registry)


def temperature_trend(influx: InfluxClient, station_id: str, hours: int = 168) -> list[dict]:
    """
    Return daily temperature summary [{date, min, mean, max}] for a station.
    """
    rows = influx.query_history(station_id, hours)
    daily: dict[str, list] = defaultdict(list)
    for row in rows:
        t = row.get("temperature")
        if t is None:
            continue
        date_str = _to_dt(row["timestamp"]).astimezone(timezone.utc).strftime("%Y-%m-%d")
        daily[date_str].append(float(t))

    result = []
    for date_str, values in sorted(daily.items()):
        result.append({
            "date": date_str,
            "min": round(min(values), 1),
            "mean": round(sum(values) / len(values), 1),
            "max": round(max(values), 1),
        })
    return result
