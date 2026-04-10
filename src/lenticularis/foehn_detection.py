"""
Föhn detection logic — v2.

Supports general per-field conditions (wind, temperature, humidity, pressure,
and any other observed field) with optional delta/trend evaluation against
historical snapshots.

Region status values (used as float in InfluxDB):
  1.0  — active   (all conditions met)
  0.5  — partial  (some conditions met)
  0.0  — inactive (data available, no conditions met)
 -1.0  — no_data  (no station data available)
"""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

_logger = logging.getLogger(__name__)
_FOEHN_CONFIG_PATH = Path("data/foehn_config.json")

# ---------------------------------------------------------------------------
# Direction constants
# ---------------------------------------------------------------------------
_NE, _SE   =  45.0, 135.0   # NE → SE  (Wallis)
_SE2, _SW  = 112.5, 247.5   # ESE → WSW (most föhn corridors)
_ESE, _SSE = 112.5, 157.5   # ESE → SSE (Guggiföhn)

# Numeric encoding used in InfluxDB
STATUS_ACTIVE   = 1.0
STATUS_PARTIAL  = 0.5
STATUS_INACTIVE = 0.0
STATUS_NO_DATA  = -1.0

STATUS_TO_NUMERIC = {
    "active":   STATUS_ACTIVE,
    "partial":  STATUS_PARTIAL,
    "inactive": STATUS_INACTIVE,
    "no_data":  STATUS_NO_DATA,
}

# Supported fields and operators exposed to the editor
FOEHN_FIELDS = (
    "wind_speed", "wind_gust", "wind_direction",
    "temperature", "humidity", "pressure_qff",
)
FOEHN_OPERATORS = (
    ">", "<", ">=", "<=", "=",
    "between", "not_between", "in_direction_range",
)
# Valid lookback window sizes for delta/trend conditions (hours)
LOOKBACK_OPTIONS = (1, 2, 3, 6)

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class FoehnCondition:
    """A single indicator condition within a föhn region.

    Set ``lookback_h`` to evaluate a delta rather than the current value:
    the condition tests ``current_field − field_N_hours_ago`` vs the threshold.
    Useful for detecting trends like a rapid humidity drop (föhn arriving).
    """
    __slots__ = (
        "station_id", "field", "operator", "value_a", "value_b",
        "lookback_h", "label", "result_colour",
    )

    def __init__(
        self,
        station_id: str,
        field: str,
        operator: str,
        value_a: float,
        value_b: Optional[float] = None,
        lookback_h: Optional[int] = None,
        label: Optional[str] = None,
        result_colour: str = "red",
    ) -> None:
        self.station_id    = station_id
        self.field         = field
        self.operator      = operator
        self.value_a       = float(value_a)
        self.value_b       = float(value_b) if value_b is not None else None
        self.lookback_h    = int(lookback_h) if lookback_h is not None else None
        self.label         = label or station_id
        self.result_colour = result_colour


class FoehnRegion:
    __slots__ = ("key", "label", "description", "conditions", "pressure_pair")

    def __init__(
        self,
        key: str,
        label: str,
        description: str,
        conditions: list[FoehnCondition],
        pressure_pair: Optional[dict] = None,
    ) -> None:
        self.key           = key
        self.label         = label
        self.description   = description
        self.conditions    = conditions
        # Optional QFF pressure pair for this region.
        # Format: {south_id, north_id, south_label, north_label, threshold, key}
        self.pressure_pair = pressure_pair


# Backward-compat alias so any external code importing ``Region`` still works.
Region = FoehnRegion

# ---------------------------------------------------------------------------
# Helper: expand an old-style wind Cond (speed + optional direction) into
# one or two FoehnCondition objects.
# ---------------------------------------------------------------------------

def _wc(
    station_id: str,
    label: str,
    speed: float,
    dir_low: Optional[float] = None,
    dir_high: Optional[float] = None,
    colour: str = "red",
) -> list[FoehnCondition]:
    conds: list[FoehnCondition] = [
        FoehnCondition(station_id, "wind_gust", ">=", speed,
                       label=f"{label} — Gust", result_colour=colour),
    ]
    if dir_low is not None and dir_high is not None:
        conds.append(
            FoehnCondition(station_id, "wind_direction", "in_direction_range",
                           dir_low, dir_high,
                           label=f"{label} — Direction", result_colour=colour),
        )
    return conds


# ---------------------------------------------------------------------------
# Föhn region definitions (system defaults)
# ---------------------------------------------------------------------------

REGIONS: list[FoehnRegion] = [
    FoehnRegion("haslital", "Haslital", "Brienz / Meiringen Föhn-Korridor", conditions=[
        *_wc("meteoswiss-GRH", "GRH (Grimsel Hospiz)", 35, _SE2, _SW),
        *_wc("slf-GUT1",       "GUT1 (Guttannen)",      40, _SE2, _SW),
        *_wc("slf-SCB2",       "SCB2 (Schattenhalb)",   40, _SE2, _SW),
    ]),
    FoehnRegion("beo", "BEO Föhn", "Haslital + Jungfraujoch — ganzes Berner Oberland", conditions=[
        *_wc("meteoswiss-GRH", "GRH (Grimsel Hospiz)", 35, _SE2, _SW),
        *_wc("slf-GUT1",       "GUT1 (Guttannen)",      40, _SE2, _SW),
        *_wc("slf-SCB2",       "SCB2 (Schattenhalb)",   40, _SE2, _SW),
        *_wc("meteoswiss-JUN", "JUN (Jungfraujoch)",    40, _SE2, _SW),
    ]),
    FoehnRegion("wallis", "Wallis", "Rhône-Tal Föhn aus NE–SE", conditions=[
        *_wc("meteoswiss-VIS", "VIS (Visp)", 40, _NE, _SE),
    ]),
    FoehnRegion("reussthal", "Reussthal", "Uri / Reuss-Tal Föhn", conditions=[
        *_wc("meteoswiss-GUE", "GUE (Gurtnellen)", 40, _SE2, _SW),
        *_wc("slf-SCA1",       "SCA1 (Schächental)", 40),
    ]),
    FoehnRegion("rheintal", "Rheintal", "Graubünden / Rheintal Föhn", conditions=[
        *_wc("meteoswiss-CHU", "CHU (Chur)",    40, _SE2, _SW),
        *_wc("slf-TAM1",       "TAM1 (Tamins)",  40, _SE2, _SW),
    ]),
    FoehnRegion("guggi", "Guggiföhn", "Enger ESE–SSE-Sektor am Jungfraujoch + Lohner", conditions=[
        *_wc("meteoswiss-JUN", "JUN (Jungfraujoch)", 40, _ESE, _SSE),
        *_wc("slf-LHO2",       "LHO2 (Russisprung)",  50),
    ]),
]

# Global pressure pair: OTL (Locarno/Monti, south) vs INT (Interlaken, north).
# delta = south − north:  ≥ +4 hPa → South Föhn,  ≤ −4 hPa → North Föhn
PRESSURE_PAIRS: list[dict] = [
    {
        "key":         "valley",
        "south_id":    "meteoswiss-OTL",
        "north_id":    "meteoswiss-INT",
        "south_label": "OTL (Locarno/Monti)",
        "north_label": "INT (Interlaken)",
        "threshold":   4.0,
    },
]

# Precomputed set of all station IDs for the default config
ALL_STATION_IDS: list[str] = list({
    c.station_id
    for r in REGIONS
    for c in r.conditions
} | {
    sid
    for r in REGIONS
    if r.pressure_pair
    for sid in (r.pressure_pair["south_id"], r.pressure_pair["north_id"])
} | {
    sid
    for pair in PRESSURE_PAIRS
    for sid in (pair["south_id"], pair["north_id"])
})

# ---------------------------------------------------------------------------
# Runtime config overrides (None = use hardcoded defaults above)
# ---------------------------------------------------------------------------

_runtime_regions: list[FoehnRegion] | None = None
_runtime_pressure_pairs: list[dict] | None = None


def get_regions() -> list[FoehnRegion]:
    return _runtime_regions if _runtime_regions is not None else REGIONS


def get_pressure_pairs() -> list[dict]:
    return _runtime_pressure_pairs if _runtime_pressure_pairs is not None else PRESSURE_PAIRS


def get_all_station_ids() -> list[str]:
    return list({
        c.station_id
        for r in get_regions()
        for c in r.conditions
    } | {
        sid
        for r in get_regions()
        if r.pressure_pair
        for sid in (r.pressure_pair["south_id"], r.pressure_pair["north_id"])
    } | {
        sid
        for pair in get_pressure_pairs()
        for sid in (pair["south_id"], pair["north_id"])
    })


def regions_from_config(config: Optional[dict]) -> list[FoehnRegion]:
    """Return regions from a user config dict, or the system defaults if None."""
    if config and "regions" in config:
        return [_region_from_dict(r) for r in config["regions"]]
    return get_regions()


def pressure_pairs_from_config(config: Optional[dict]) -> list[dict]:
    """Return pressure pairs from a user config dict, or the system defaults if None."""
    if config and "pressure_pairs" in config:
        return list(config["pressure_pairs"])
    return get_pressure_pairs()


def get_all_station_ids_from_config(config: Optional[dict] = None) -> list[str]:
    """Return all station IDs needed to evaluate the given config (or system defaults)."""
    if config is None:
        return get_all_station_ids()
    ids: set[str] = set()
    for r in regions_from_config(config):
        for c in r.conditions:
            ids.add(c.station_id)
        if r.pressure_pair:
            ids.add(r.pressure_pair["south_id"])
            ids.add(r.pressure_pair["north_id"])
    for pair in pressure_pairs_from_config(config):
        ids.add(pair["south_id"])
        ids.add(pair["north_id"])
    return list(ids)


def get_required_lookback_hours(config: Optional[dict] = None) -> set[int]:
    """Return the set of unique lookback_h values needed for delta conditions."""
    hours: set[int] = set()
    for r in regions_from_config(config):
        for c in r.conditions:
            if c.lookback_h:
                hours.add(c.lookback_h)
    return hours


def get_all_pressure_pairs_from_config(config: Optional[dict] = None) -> list[dict]:
    """Return all pressure pairs: global + per-region (deduped by key)."""
    pairs = list(pressure_pairs_from_config(config))
    seen  = {p["key"] for p in pairs}
    for r in regions_from_config(config):
        rp = r.pressure_pair
        if not rp:
            continue
        key = rp.get("key") or r.key
        if key not in seen:
            pairs.append({**rp, "key": key})
            seen.add(key)
    return pairs


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------

def _foehn_condition_to_dict(c: FoehnCondition) -> dict:
    return {
        "station_id":    c.station_id,
        "label":         c.label,
        "field":         c.field,
        "operator":      c.operator,
        "value_a":       c.value_a,
        "value_b":       c.value_b,
        "lookback_h":    c.lookback_h,
        "result_colour": c.result_colour,
    }


def _foehn_condition_from_dict(d: dict) -> FoehnCondition:
    return FoehnCondition(
        station_id    = d["station_id"],
        field         = d["field"],
        operator      = d["operator"],
        value_a       = float(d["value_a"]),
        value_b       = float(d["value_b"]) if d.get("value_b") is not None else None,
        lookback_h    = int(d["lookback_h"]) if d.get("lookback_h") else None,
        label         = d.get("label") or d["station_id"],
        result_colour = d.get("result_colour", "red"),
    )


def _legacy_wind_conds_from_dict(d: dict) -> list[FoehnCondition]:
    """Convert old-format {speed_min, dir_low?, dir_high?} to new FoehnCondition(s)."""
    base_label = d.get("label", d["station_id"])
    result = [
        FoehnCondition(d["station_id"], "wind_gust", ">=", float(d["speed_min"]),
                       label=f"{base_label} — Gust"),
    ]
    if d.get("dir_low") is not None and d.get("dir_high") is not None:
        result.append(
            FoehnCondition(d["station_id"], "wind_direction", "in_direction_range",
                           float(d["dir_low"]), float(d["dir_high"]),
                           label=f"{base_label} — Direction"),
        )
    return result


def _region_to_dict(r: FoehnRegion) -> dict:
    return {
        "key":          r.key,
        "label":        r.label,
        "description":  r.description,
        "pressure_pair": r.pressure_pair,
        "conditions":   [_foehn_condition_to_dict(c) for c in r.conditions],
    }


def _region_from_dict(d: dict) -> FoehnRegion:
    conditions: list[FoehnCondition] = []
    for c in d.get("conditions", []):
        if "field" in c and "operator" in c:
            # New v2 format
            conditions.append(_foehn_condition_from_dict(c))
        elif "speed_min" in c:
            # Legacy v1 format — silently upgrade
            conditions.extend(_legacy_wind_conds_from_dict(c))
    return FoehnRegion(
        key           = d["key"],
        label         = d["label"],
        description   = d.get("description", ""),
        conditions    = conditions,
        pressure_pair = d.get("pressure_pair") or None,
    )


def get_foehn_config_dict() -> dict:
    """Return the current (possibly overridden) config as a serialisable dict."""
    return {
        "regions":        [_region_to_dict(r) for r in get_regions()],
        "pressure_pairs": [dict(p) for p in get_pressure_pairs()],
    }


def set_foehn_config(data: dict) -> None:
    """Apply a new config dict at runtime and persist it to JSON."""
    global _runtime_regions, _runtime_pressure_pairs
    _runtime_regions        = [_region_from_dict(r) for r in data.get("regions", [])]
    _runtime_pressure_pairs = [dict(p) for p in data.get("pressure_pairs", [])]
    _FOEHN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FOEHN_CONFIG_PATH.write_text(
        json.dumps(get_foehn_config_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _logger.info("foehn_detection: config saved to %s", _FOEHN_CONFIG_PATH)


def reset_foehn_config() -> None:
    """Remove the persisted override and revert to hardcoded defaults."""
    global _runtime_regions, _runtime_pressure_pairs
    _runtime_regions = _runtime_pressure_pairs = None
    if _FOEHN_CONFIG_PATH.exists():
        _FOEHN_CONFIG_PATH.unlink()
    _logger.info("foehn_detection: config reset to defaults")


def _try_load_foehn_config() -> None:
    if not _FOEHN_CONFIG_PATH.exists():
        return
    try:
        data = json.loads(_FOEHN_CONFIG_PATH.read_text(encoding="utf-8"))
        set_foehn_config(data)
        _logger.info("foehn_detection: loaded config from %s", _FOEHN_CONFIG_PATH)
    except Exception as exc:  # noqa: BLE001
        _logger.warning("foehn_detection: could not load %s: %s", _FOEHN_CONFIG_PATH, exc)


# ---------------------------------------------------------------------------
# Virtual stations written to InfluxDB by the FoehnCollector
# ---------------------------------------------------------------------------
VIRTUAL_STATIONS = [
    {"station_id": "foehn-haslital",  "name": "Föhn Monitor — Haslital",  "latitude": 46.740810, "longitude": 8.126922, "elevation": 595,  "canton": "BE"},
    {"station_id": "foehn-beo",       "name": "Föhn Monitor — BEO Föhn",  "latitude": 46.701998, "longitude": 7.919331, "elevation": 570,  "canton": "BE"},
    {"station_id": "foehn-wallis",    "name": "Föhn Monitor — Wallis",     "latitude": 46.295,    "longitude": 7.883,    "elevation": 640,  "canton": "VS"},
    {"station_id": "foehn-reussthal", "name": "Föhn Monitor — Reussthal", "latitude": 46.794,    "longitude": 8.628,    "elevation": 470,  "canton": "UR"},
    {"station_id": "foehn-rheintal",  "name": "Föhn Monitor — Rheintal",  "latitude": 46.849,    "longitude": 9.529,    "elevation": 593,  "canton": "GR"},
    {"station_id": "foehn-guggi",     "name": "Föhn Monitor — Guggiföhn", "latitude": 46.572499, "longitude": 7.922918, "elevation": 3466, "canton": "BE"},
    {"station_id": "foehn-overall",   "name": "Föhn Monitor — Overall",   "latitude": 46.800,    "longitude": 8.200,    "elevation": 500,  "canton": "BE"},
]

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_DIR_NAMES: dict[float, str] = {
    0: "N", 22.5: "NNE", 45: "NE", 67.5: "ENE", 90: "E",
    112.5: "ESE", 135: "SE", 157.5: "SSE", 180: "S",
    202.5: "SSW", 225: "SW", 247.5: "WSW", 270: "W", 315: "NW",
}


def in_range(deg: Optional[float], low: float, high: float) -> bool:
    """True when ``deg`` falls within [low, high], handling 360° wraparound."""
    if deg is None:
        return False
    if low <= high:
        return low <= deg <= high
    return deg >= low or deg <= high


def dir_label(low: Optional[float], high: Optional[float]) -> str:
    if low is None or high is None:
        return "any"
    lo = _DIR_NAMES.get(low,  f"{low:.0f}°")
    hi = _DIR_NAMES.get(high, f"{high:.0f}°")
    return f"{lo}–{hi}"


def _apply_operator(
    value: float,
    operator: str,
    value_a: float,
    value_b: Optional[float] = None,
) -> bool:
    if operator == ">":               return value > value_a
    if operator == ">=":              return value >= value_a
    if operator == "<":               return value < value_a
    if operator == "<=":              return value <= value_a
    if operator == "=":               return value == value_a
    if operator == "between":         return value_b is not None and value_a <= value <= value_b
    if operator == "not_between":     return value_b is not None and not (value_a <= value <= value_b)
    if operator == "in_direction_range": return value_b is not None and in_range(value, value_a, value_b)
    return False


def _field_unit(field: str) -> str:
    return {
        "wind_speed": "km/h", "wind_gust": "km/h", "wind_direction": "°",
        "temperature": "°C", "humidity": "%", "pressure_qff": "hPa",
    }.get(field, "")


def _cond_desc(operator: str, value_a: float, value_b: Optional[float],
               unit: str, lookback_h: Optional[int]) -> str:
    """Human-readable summary of the threshold, e.g. '≥ 35 km/h' or 'Δ2h < −10 %'."""
    prefix = f"Δ{lookback_h}h " if lookback_h else ""
    if operator == "in_direction_range":
        lo = _DIR_NAMES.get(value_a, f"{value_a:.0f}°")
        hi = _DIR_NAMES.get(value_b, f"{value_b:.0f}°") if value_b is not None else "?"
        return f"Direction {lo}–{hi}"
    if operator == "between":
        return f"{prefix}{value_a}–{value_b} {unit}"
    if operator == "not_between":
        return f"{prefix}not {value_a}–{value_b} {unit}"
    op_str = {">=": "≥", "<=": "≤", "=": "=", ">": ">", "<": "<"}.get(operator, operator)
    return f"{prefix}{op_str} {value_a:.4g} {unit}"


# ---------------------------------------------------------------------------
# Pressure pair evaluation
# ---------------------------------------------------------------------------

def eval_pressure_pair(pair: dict, latest: dict[str, dict]) -> dict:
    """Evaluate one QFF pressure pair and return both direction signals.

    delta = south_qff − north_qff:
      ≥ +threshold  → South Föhn pressure signal
      ≤ −threshold  → North Föhn pressure signal
    """
    s_data    = latest.get(pair["south_id"])
    n_data    = latest.get(pair["north_id"])
    s_p       = s_data.get("pressure_qff") if s_data else None
    n_p       = n_data.get("pressure_qff") if n_data else None
    threshold = float(pair.get("threshold", 4.0))
    delta     = round(s_p - n_p, 2) if (s_p is not None and n_p is not None) else None
    south_active = delta is not None and delta >= threshold
    north_active = delta is not None and delta <= -threshold
    return {
        "south_station_id": pair["south_id"],
        "north_station_id": pair["north_id"],
        "south_label":      pair.get("south_label", pair["south_id"]),
        "north_label":      pair.get("north_label", pair["north_id"]),
        "south_hpa":        round(s_p, 2) if s_p is not None else None,
        "north_hpa":        round(n_p, 2) if n_p is not None else None,
        "delta_hpa":        delta,
        "threshold_hpa":    threshold,
        "south_active":     south_active,
        "north_active":     north_active,
        "active":           south_active or north_active,
    }


# ---------------------------------------------------------------------------
# Condition evaluation
# ---------------------------------------------------------------------------

def eval_foehn_condition(
    cond: FoehnCondition,
    latest: dict[str, dict],
    historical: Optional[dict[int, dict[str, dict]]] = None,
) -> dict:
    """Evaluate a single föhn condition.

    Args:
        cond:       The condition to evaluate.
        latest:     {station_id: {field: value, ...}} — current measurements.
        historical: {lookback_h: {station_id: {field: value}}} — past snapshots.
                    Required for delta conditions (cond.lookback_h set).
    """
    unit      = _field_unit(cond.field)
    desc      = _cond_desc(cond.operator, cond.value_a, cond.value_b, unit, cond.lookback_h)
    is_delta  = bool(cond.lookback_h)
    data      = latest.get(cond.station_id)

    base = {
        "station_id":    cond.station_id,
        "label":         cond.label,
        "field":         cond.field,
        "operator":      cond.operator,
        "value_a":       cond.value_a,
        "value_b":       cond.value_b,
        "lookback_h":    cond.lookback_h,
        "cond_desc":     desc,
        "unit":          unit,
        "is_delta":      is_delta,
        "result_colour": cond.result_colour,
    }

    if data is None:
        return {**base, "actual_value": None, "actual_raw": None,
                "prev_value": None, "met": False, "data_available": False, "timestamp": None}

    raw_value: Optional[float] = data.get(cond.field)
    prev_value: Optional[float] = None
    eval_value: Optional[float] = None

    if is_delta:
        # Delta condition: evaluate (current − historical) vs threshold
        if historical and cond.lookback_h in historical:
            hist_data = historical[cond.lookback_h].get(cond.station_id)
            if hist_data is not None:
                prev_raw = hist_data.get(cond.field)
                if raw_value is not None and prev_raw is not None:
                    eval_value = round(raw_value - prev_raw, 2)
                    prev_value = prev_raw
        # eval_value stays None if historical not available → data_available=True but met=False
    else:
        eval_value = raw_value

    if eval_value is None:
        # Data exists for current but delta can't be computed (no historical snapshot)
        return {**base, "actual_value": None,
                "actual_raw": round(raw_value, 2) if raw_value is not None else None,
                "prev_value": None, "met": False, "data_available": True,
                "timestamp": data["timestamp"].isoformat() if data.get("timestamp") else None}

    met = _apply_operator(eval_value, cond.operator, cond.value_a, cond.value_b)

    return {
        **base,
        "actual_value":  round(eval_value, 2) if isinstance(eval_value, float) else eval_value,
        "actual_raw":    round(raw_value, 2) if raw_value is not None else None,
        "prev_value":    round(prev_value, 2) if prev_value is not None else None,
        "met":           met,
        "data_available": True,
        "timestamp":     data["timestamp"].isoformat() if data.get("timestamp") else None,
    }


def eval_region(
    region: FoehnRegion,
    latest: dict[str, dict],
    historical: Optional[dict[int, dict[str, dict]]] = None,
) -> dict:
    conds    = [eval_foehn_condition(c, latest, historical) for c in region.conditions]
    total    = len(conds)
    met      = sum(1 for c in conds if c["met"])
    has_data = sum(1 for c in conds if c["data_available"])

    if total == 0 or has_data == 0:
        status = "no_data"
    elif met == total:
        status = "active"
    elif met > 0:
        status = "partial"
    else:
        status = "inactive"

    pressure = (
        eval_pressure_pair(region.pressure_pair, latest)
        if region.pressure_pair else None
    )

    return {
        "key":              region.key,
        "label":            region.label,
        "description":      region.description,
        "status":           status,
        "conditions_met":   met,
        "conditions_total": total,
        "conditions":       conds,
        "pressure":         pressure,
    }


def build_all_pressures(
    latest: dict[str, dict],
    pairs: Optional[list[dict]] = None,
) -> list[dict]:
    """Evaluate global pressure pairs against live/forecast/observation data."""
    result = []
    for pair in (pairs if pairs is not None else get_pressure_pairs()):
        entry = eval_pressure_pair(pair, latest)
        entry["key"] = pair["key"]
        result.append(entry)
    return result


_try_load_foehn_config()


def build_response(
    regions: list[dict],
    pressures: list[dict],
    assessed_at: str,
    extra: Optional[dict] = None,
) -> dict:
    """Shared response builder for /status, /forecast, and /observation."""
    active_regions  = [r["key"] for r in regions if r["status"] == "active"]
    partial_regions = [r["key"] for r in regions if r["status"] == "partial"]

    south_pressure_risk = any(p.get("south_active") for p in pressures)
    north_pressure_risk = any(p.get("north_active") for p in pressures)
    for r in regions:
        rp = r.get("pressure")
        if rp:
            if rp.get("south_active"):
                south_pressure_risk = True
            if rp.get("north_active"):
                north_pressure_risk = True

    pressure_risk = south_pressure_risk  # backward-compat

    if active_regions:
        overall_status = "active"
    elif south_pressure_risk:
        overall_status = "risk"
    elif north_pressure_risk:
        overall_status = "north_risk"
    elif partial_regions:
        overall_status = "partial"
    else:
        overall_status = "inactive"

    resp = {
        "assessed_at": assessed_at,
        "overall": {
            "status":           overall_status,
            "active_regions":   active_regions,
            "partial_regions":  partial_regions,
            "pressure_risk":    pressure_risk,
            "south_foehn_risk": south_pressure_risk,
            "north_foehn_risk": north_pressure_risk,
        },
        "regions":   regions,
        "pressures": pressures,
    }
    if extra:
        resp.update(extra)
    return resp
