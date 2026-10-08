"""
Tool logic for the public MCP server. Pure-ish async methods: validate → read → shape.

No MCP-SDK imports here, so the logic is unit-testable without a transport. Every error is a
``McpToolError(code, message)`` whose message tells the caller what to try next. Blocking
InfluxDB calls always go through ``asyncio.to_thread`` (04-constraints: Performance).
"""
from __future__ import annotations

import asyncio
import logging
import math
from datetime import datetime, timedelta, timezone
from typing import Any, Callable, Optional

from lenticularis.config import McpConfig
from lenticularis.mcp_server.registry import McpRegistry
from lenticularis.mcp_server.sanitize import (
    FORECAST_FIELDS,
    OBSERVATION_FIELDS,
    UNITS,
    age_minutes,
    as_utc,
    clean_values,
    is_valid_station_id,
    iso_z,
    units_for,
)

logger = logging.getLogger(__name__)

FORECAST_NOTE = (
    "Hourly ICON-CH ensemble forecast (median value; *_min/*_max give the ensemble spread). "
    "Hours with no usable model data are listed in missing_hours and are never interpolated. "
    "The model hands over from ICON-CH1 to ICON-CH2 at about h+33 and some runs have no data for "
    "roughly h+19..h+33; Lenticularis fills such gaps from the previous model runs when it can."
)

# Window sizes tried (smallest first) when picking a history resolution.
_RESOLUTIONS: list[tuple[str, int]] = [
    ("10m", 10), ("30m", 30), ("1h", 60), ("3h", 180), ("6h", 360), ("12h", 720), ("1d", 1440),
]
_AGGREGATION_LABEL = {
    "wind_gust": "max", "precipitation": "sum", "wind_direction": "last", "snow_depth": "last",
}


class McpToolError(Exception):
    """A caller-facing error: ``code`` follows the project vocabulary (07-api-conventions)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def _ranges(hours: list[datetime]) -> list[str]:
    """Collapse sorted hourly datetimes into 'start/end' ISO ranges."""
    out: list[str] = []
    start = prev = None
    for h in hours:
        if start is None:
            start = prev = h
        elif h - prev == timedelta(hours=1):
            prev = h
        else:
            out.append(f"{iso_z(start)}/{iso_z(prev)}")
            start = prev = h
    if start is not None:
        out.append(f"{iso_z(start)}/{iso_z(prev)}")
    return out


class PublicWeatherTools:
    """Read-only tools over the verified registry. ``state`` is the FastAPI ``app.state``."""

    def __init__(self, state_getter: Callable[[], Any], cfg: McpConfig, version: str = "") -> None:
        self._state = state_getter
        self._cfg = cfg
        self._version = version

    # ------------------------------------------------------------------ helpers

    @property
    def registry(self) -> McpRegistry:
        reg = getattr(self._state(), "mcp_registry", None)
        if reg is None:
            raise McpToolError("UNAVAILABLE", "Station registry not ready yet. Retry in a few seconds.")
        return reg

    @property
    def influx(self) -> Any:
        influx = getattr(self._state(), "influx", None)
        if influx is None:
            raise McpToolError("UNAVAILABLE", "Weather database is not available. Retry later.")
        return influx

    def _resolve(self, station_id: str) -> str:
        if not is_valid_station_id(station_id):
            raise McpToolError(
                "VALIDATION_FAILED",
                f"Invalid station_id {station_id!r}. Use search_stations to find a valid id.",
            )
        canon = self.registry.resolve(station_id)
        if canon is None:
            raise McpToolError(
                "ENTITY_NOT_FOUND",
                f"Unknown station {station_id!r}. Use search_stations (by name or lat/lon) to find ids.",
            )
        return canon

    def _station_meta(self, canon: str) -> dict[str, Any]:
        s = self.registry.stations[canon]
        return {
            "station_id": s.station_id,
            "name": s.name,
            "network": s.network,
            "canton": s.canton,
            "latitude": round(s.latitude, 5),
            "longitude": round(s.longitude, 5),
            "elevation_m": s.elevation,
        }

    # ------------------------------------------------------------------ US1

    async def search_stations(
        self,
        query: Optional[str] = None,
        lat: Optional[float] = None,
        lon: Optional[float] = None,
        radius_km: Optional[float] = None,
        network: Optional[str] = None,
        canton: Optional[str] = None,
        limit: int = 10,
    ) -> dict[str, Any]:
        if (lat is None) != (lon is None):
            raise McpToolError("VALIDATION_FAILED", "Provide both lat and lon, or neither.")
        if lat is not None and not (-90 <= lat <= 90 and -180 <= lon <= 180):  # type: ignore[operator]
            raise McpToolError("VALIDATION_FAILED", "lat must be within [-90, 90] and lon within [-180, 180].")
        if radius_km is not None:
            if lat is None:
                raise McpToolError("VALIDATION_FAILED", "radius_km needs lat and lon.")
            if not (0 < radius_km <= 200):
                raise McpToolError("VALIDATION_FAILED", "radius_km must be in (0, 200].")
        if lat is not None and radius_km is None:
            radius_km = 25.0
        reg = self.registry
        if network and network not in reg.verified_networks:
            raise McpToolError(
                "VALIDATION_FAILED",
                f"Unknown network {network!r}. Available: {sorted(reg.verified_networks)}.",
            )
        limit = max(1, min(int(limit), self._cfg.max_search_results))

        found = reg.search(query, lat, lon, radius_km, network, canton)
        shown = found[:limit]
        stations = []
        for s, dist in shown:
            item = self._station_meta(s.station_id)
            if dist is not None:
                item["distance_km"] = round(dist, 1)
            stations.append(item)
        out: dict[str, Any] = {"count": len(shown), "total_matches": len(found),
                               "truncated": len(found) > len(shown), "stations": stations}
        if not found:
            out["hint"] = ("No station matched. Try a shorter name, a larger radius_km, or search by "
                           "lat/lon. Names are matched ignoring accents (Zürich = Zuerich = Zurich).")
        return out

    # ------------------------------------------------------------------ US2

    async def get_current_weather(self, station_id: str) -> dict[str, Any]:
        canon = self._resolve(station_id)
        members = self.registry.member_ids(canon)
        data = await asyncio.to_thread(self.influx.query_latest_virtual, members)
        meta = self._station_meta(canon)
        base: dict[str, Any] = {"station": meta, "source": meta["network"], "timezone": "UTC"}
        if not data:
            return {**base, "time": None, "age_minutes": None, "stale": True, "values": {}, "units": {},
                    "message": "No measurement received in the last 24 hours."}
        ts = data.get("timestamp")
        values = clean_values({k: v for k, v in data.items() if k != "timestamp"}, members)
        age = age_minutes(ts)
        out = {**base, "time": iso_z(ts), "age_minutes": age,
               "stale": age is None or age > self._cfg.stale_after_minutes,
               "values": values, "units": units_for(set(values))}
        if out["stale"]:
            out["message"] = (f"Latest measurement is {age} minutes old (older than "
                              f"{self._cfg.stale_after_minutes} min) — treat as not current.")
        return out

    # ------------------------------------------------------------------ US4

    async def get_forecast(self, station_id: str, hours: int = 48) -> dict[str, Any]:
        canon = self._resolve(station_id)
        if not (1 <= hours <= 120):
            raise McpToolError("VALIDATION_FAILED", "hours must be between 1 and 120.")
        raw = await asyncio.to_thread(
            self.influx.query_forecast_for_stations, [canon], hours, True
        )
        by_time = raw.get(canon, {}) or {}

        rows: list[dict[str, Any]] = []
        present_hours: set[datetime] = set()
        issued: Optional[str] = None
        source = model = None
        keys: set[str] = set()
        for ts_iso in sorted(by_time):
            fields = by_time[ts_iso]
            values = clean_values(fields, canon, FORECAST_FIELDS)
            if not values:
                continue
            vt = as_utc(ts_iso)
            if vt is None:
                continue
            rows.append({"time": iso_z(vt), **values})
            keys |= set(values)
            present_hours.add(vt.replace(minute=0, second=0, microsecond=0))
            source = source or fields.get("source")
            model = model or fields.get("model")
            init = fields.get("init_date")
            if init and (issued is None or init > issued):
                issued = init

        now = datetime.now(timezone.utc)
        first = now.replace(minute=0, second=0, microsecond=0)
        if first < now:
            first += timedelta(hours=1)
        horizon_end = now + timedelta(hours=hours)
        missing: list[datetime] = []
        h = first
        while h <= horizon_end:
            if h not in present_hours:
                missing.append(h)
            h += timedelta(hours=1)

        forecast_issued = None
        if issued:
            try:
                forecast_issued = iso_z(datetime.strptime(issued, "%Y-%m-%dT%H").replace(tzinfo=timezone.utc))
            except ValueError:
                forecast_issued = issued
        return {
            "station": self._station_meta(canon),
            "source": source, "model": model, "forecast_issued": forecast_issued, "timezone": "UTC",
            "hours_requested": hours, "count": len(rows),
            "units": units_for(keys), "data": rows,
            "missing_hours": {"count": len(missing), "ranges": _ranges(missing)},
            "note": FORECAST_NOTE,
        }

    # ------------------------------------------------------------------ US3

    def _pick_resolution(self, span: timedelta, requested: str) -> tuple[str, int, bool]:
        """Return (every, minutes, bumped). Never exceeds ``max_history_points`` points."""
        total_min = span.total_seconds() / 60
        cap = self._cfg.max_history_points
        names = [r[0] for r in _RESOLUTIONS]
        start_idx = 0
        if requested != "auto":
            start_idx = names.index(requested)
        for idx in range(start_idx, len(_RESOLUTIONS)):
            every, minutes = _RESOLUTIONS[idx]
            if math.ceil(total_min / minutes) <= cap:
                return every, minutes, idx != start_idx
        every, minutes = _RESOLUTIONS[-1]
        return every, minutes, True

    async def get_weather_history(
        self,
        station_id: str,
        hours: Optional[int] = None,
        start: Optional[str] = None,
        end: Optional[str] = None,
        fields: Optional[list[str]] = None,
        resolution: str = "auto",
    ) -> dict[str, Any]:
        canon = self._resolve(station_id)
        now = datetime.now(timezone.utc)
        if hours is not None and (start or end):
            raise McpToolError("VALIDATION_FAILED", "Use either hours or start/end, not both.")
        if hours is not None:
            if not (1 <= hours <= 720):
                raise McpToolError("VALIDATION_FAILED", "hours must be between 1 and 720.")
            t_end, t_start = now, now - timedelta(hours=hours)
        elif start or end:
            if not (start and end):
                raise McpToolError("VALIDATION_FAILED", "Provide both start and end (ISO-8601).")
            t_start, t_end = as_utc(start), as_utc(end)
            if t_start is None or t_end is None:
                raise McpToolError("VALIDATION_FAILED", "start/end must be ISO-8601, e.g. 2026-10-01T00:00:00Z.")
            if t_end > now:
                t_end = now
            if t_start >= t_end:
                raise McpToolError("VALIDATION_FAILED", "start must be before end (and in the past).")
            if t_end - t_start > timedelta(days=365):
                raise McpToolError("VALIDATION_FAILED", "Range too long: maximum is 365 days.")
        else:
            t_end, t_start = now, now - timedelta(hours=24)

        names = ["auto"] + [r[0] for r in _RESOLUTIONS]
        if resolution not in names:
            raise McpToolError("VALIDATION_FAILED", f"resolution must be one of {names}.")
        wanted = list(fields) if fields else list(OBSERVATION_FIELDS)
        bad = [f for f in wanted if f not in OBSERVATION_FIELDS]
        if bad:
            raise McpToolError("VALIDATION_FAILED", f"Unknown fields {bad}. Allowed: {list(OBSERVATION_FIELDS)}.")

        every, _minutes, bumped = self._pick_resolution(t_end - t_start, resolution)
        members = self.registry.member_ids(canon)
        rows_raw = await asyncio.to_thread(
            self.influx.query_history_range, members, t_start, t_end, every, wanted
        )
        rows: list[dict[str, Any]] = []
        keys: set[str] = set()
        for r in rows_raw:
            values = clean_values({k: v for k, v in r.items() if k != "timestamp"}, members)
            if not values:
                continue
            rows.append({"time": iso_z(r.get("timestamp")), **values})
            keys |= set(values)
        out: dict[str, Any] = {
            "station": self._station_meta(canon), "source": self.registry.stations[canon].network,
            "start": iso_z(t_start), "end": iso_z(t_end), "timezone": "UTC",
            "resolution": every, "resolution_bumped": bumped, "count": len(rows),
            "aggregation": {k: _AGGREGATION_LABEL.get(k, "mean") for k in sorted(keys)},
            "units": units_for(keys), "data": rows,
        }
        if bumped:
            out["note"] = (f"Resolution was coarsened to {every} to stay within "
                           f"{self._cfg.max_history_points} points.")
        if not rows:
            out["hint"] = "No data in this range for this station. Try a different period or station."
        return out

    # ------------------------------------------------------------------ US5

    async def get_foehn_status(self, at: Optional[str] = None) -> dict[str, Any]:
        # Imported lazily: foehn_detection pulls in config/IO helpers only needed here.
        from lenticularis import foehn_detection as fd

        now = datetime.now(timezone.utc)
        vt: Optional[datetime] = None
        if at:
            vt = as_utc(at)
            if vt is None:
                raise McpToolError("VALIDATION_FAILED", "at must be ISO-8601, e.g. 2026-10-08T12:00:00Z.")
            if vt > now + timedelta(hours=120):
                raise McpToolError("VALIDATION_FAILED", "Forecast föhn is available up to 120 hours ahead.")
            if abs((vt - now).total_seconds()) < 300:
                vt = None  # "now"

        reg = self.registry
        raw_registry = getattr(self._state(), "station_registry", {}) or {}
        influx = self.influx

        def verified(sid: str) -> bool:
            st = raw_registry.get(sid)
            return st is not None and st.network in reg.verified_networks

        # System default config only — never a pilot's. Its station list is admin-editable at
        # runtime, so unverified inputs are dropped here (they then count as "no data").
        all_ids = fd.get_all_station_ids_from_config(None)
        ids = [sid for sid in all_ids if verified(sid)]
        dropped = sorted(set(all_ids) - set(ids))
        if dropped:
            logger.warning("[Lenti:mcp] föhn inputs excluded (unverified/unknown network): %s", dropped)

        def _filter(snap: dict[str, dict]) -> dict[str, dict]:
            return {sid: v for sid, v in snap.items() if sid in set(ids)}

        def _compute() -> dict[str, Any]:
            historical: dict[int, dict[str, dict]] = {}
            if vt is None:
                latest = _filter(influx.query_latest_for_stations(ids))
                for h in fd.get_required_lookback_hours(None):
                    historical[h] = _filter(
                        influx.query_observation_snapshot_for_stations(ids, now - timedelta(hours=h))
                    )
                extra = None
            elif vt < now:
                latest = _filter(influx.query_observation_snapshot_for_stations(ids, vt))
                extra = {"is_snapshot": True, "valid_time": iso_z(vt)}
            else:
                latest = _filter(influx.query_forecast_snapshot_for_stations(ids, vt))
                extra = {"is_forecast": True, "valid_time": iso_z(vt)}
            regions = [fd.eval_region(r, latest, historical or None) for r in fd.regions_from_config(None)]
            pressures = fd.build_all_pressures(latest, fd.pressure_pairs_from_config(None))
            return fd.build_response(regions, pressures, assessed_at=iso_z(now), extra=extra)

        result = await asyncio.to_thread(_compute)
        result["inputs_excluded"] = dropped
        return result

    # ------------------------------------------------------------------ US6

    async def describe_service(self) -> dict[str, Any]:
        reg = self.registry
        by_net: dict[str, int] = {}
        for s in reg.stations.values():
            by_net[s.network] = by_net.get(s.network, 0) + 1
        return {
            "name": "Lenticularis weather service (Switzerland)",
            "version": self._version,
            "purpose": "Current, past and forecast weather for Swiss weather stations, plus föhn status.",
            "access": "Public, read-only, no login. Rate limited per caller.",
            "coverage": {"station_count": len(reg.stations), "stations_per_network": dict(sorted(by_net.items()))},
            "data_policy": ("Only stations from institutional or professional/semi-professional networks "
                            "are exposed. Privately operated stations (Wunderground, Ecowitt) are excluded. "
                            "Known-faulty sensor values are suppressed."),
            "units": UNITS,
            "timezone": "All timestamps are ISO-8601 UTC.",
            "update_cadence": {
                "observations": "about every 5-30 minutes depending on network (MeteoSwiss/Jungfraubahn 10 min, "
                                "Holfuy 5 min, SLF 30 min, METAR 15 min)",
                "forecast": "ICON-CH model refreshes about 4 times per day (≈04Z, 10Z, 16Z, 22Z)",
            },
            "limitations": [
                "Forecast frames around h+19..h+33 can be missing (ICON-CH1/CH2 hand-over); they are listed "
                "in missing_hours, never invented.",
                "Observations older than "
                f"{self._cfg.stale_after_minutes} minutes are flagged stale.",
                "Stations within a few hundred metres of each other are merged into one entry (highest-priority "
                "network wins).",
                "A database outage can look like 'no data'.",
                "Not an official forecast or warning service; do not rely on it alone for flight safety.",
            ],
            "tools": ["search_stations", "get_current_weather", "get_weather_history", "get_forecast",
                      "get_foehn_status", "describe_service"],
        }
