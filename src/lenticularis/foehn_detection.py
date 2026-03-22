"""
Föhn detection logic — shared between the API router and the scheduler collector.

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
_SE2, _SW  = 112.5, 247.5   # SE → SW sectors (most corridors): ESE…WSW
_ESE, _SSE = 112.5, 157.5   # ESE → SSE  (Guggiföhn at JUN)

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

# ---------------------------------------------------------------------------
# Data structures
# ---------------------------------------------------------------------------

class Cond:
    """Single station condition within a föhn region."""
    __slots__ = ("station_id", "label", "speed_min", "dir_low", "dir_high")

    def __init__(
        self,
        station_id: str,
        label: str,
        speed_min: float,
        dir_low: Optional[float] = None,
        dir_high: Optional[float] = None,
    ) -> None:
        self.station_id = station_id
        self.label      = label
        self.speed_min  = speed_min
        self.dir_low    = dir_low
        self.dir_high   = dir_high


class Region:
    __slots__ = ("key", "label", "description", "conditions")

    def __init__(self, key: str, label: str, description: str, conditions: list[Cond]) -> None:
        self.key         = key
        self.label       = label
        self.description = description
        self.conditions  = conditions


# ---------------------------------------------------------------------------
# Föhn region definitions
# ---------------------------------------------------------------------------

REGIONS: list[Region] = [
    Region("haslital", "Haslital", "Brienz / Meiringen Föhn-Korridor", [
        Cond("meteoswiss-GRH", "GRH (Grimsel Hospiz)",  35, _SE2, _SW),
        Cond("slf-GUT1",       "GUT1 (Guttannen)",       40, _SE2, _SW),
        Cond("slf-SCB2",       "SCB2 (Schattenhalb)",    40, _SE2, _SW),
    ]),
    Region("beo", "BEO Föhn", "Haslital + Jungfraujoch — ganzes Berner Oberland", [
        Cond("meteoswiss-GRH", "GRH (Grimsel Hospiz)",  35, _SE2, _SW),
        Cond("slf-GUT1",       "GUT1 (Guttannen)",       40, _SE2, _SW),
        Cond("slf-SCB2",       "SCB2 (Schattenhalb)",    40, _SE2, _SW),
        Cond("meteoswiss-JUN", "JUN (Jungfraujoch)",     40, _SE2, _SW),
    ]),
    Region("wallis", "Wallis", "Rhône-Tal Föhn aus NE–SE", [
        Cond("meteoswiss-VIS", "VIS (Visp)", 40, _NE, _SE),
    ]),
    Region("reussthal", "Reussthal", "Uri / Reuss-Tal Föhn", [
        Cond("meteoswiss-GUE", "GUE (Gurtnellen)", 40, _SE2, _SW),
        Cond("slf-SCA1",       "SCA1 (Schächental)", 40),
    ]),
    Region("rheintal", "Rheintal", "Graubünden / Rheintal Föhn", [
        Cond("meteoswiss-CHU", "CHU (Chur)",   40, _SE2, _SW),
        Cond("slf-TAM1",       "TAM1 (Tamins)", 40, _SE2, _SW),
    ]),
    Region("guggi", "Guggiföhn", "Enger ESE–SSE-Sektor am Jungfraujoch + Lohner", [
        Cond("meteoswiss-JUN", "JUN (Jungfraujoch)", 40, _ESE, _SSE),
        Cond("slf-LHO2",       "LHO2 (Russisprung)",  50),
    ]),
]

# Pressure-based detection: OTL (Locarno/Monti, south) vs INT (Interlaken, north).
# Both stations are at valley/low-elevation level, giving a clean cross-alpine gradient.
PRESSURE_PAIRS: list[dict] = [
    {
        "key":         "valley",
        "south_id":    "meteoswiss-OTL",
        "north_id":    "meteoswiss-INT",
        "south_label": "OTL (Locarno/Monti)",
        "north_label": "INT (Interlaken)",
        "threshold":   4.0,   # hPa
    },
]

# All station IDs needed for a full evaluation (default, pre-computed)
ALL_STATION_IDS: list[str] = list({
    c.station_id
    for r in REGIONS
    for c in r.conditions
} | {
    sid
    for pair in PRESSURE_PAIRS
    for sid in (pair["south_id"], pair["north_id"])
})

# ---------------------------------------------------------------------------
# Runtime config overrides (None = use hardcoded defaults above)
# ---------------------------------------------------------------------------

_runtime_regions: list[Region] | None = None
_runtime_pressure_pairs: list[dict] | None = None


def get_regions() -> list[Region]:
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
        for pair in get_pressure_pairs()
        for sid in (pair["south_id"], pair["north_id"])
    })


def _region_to_dict(r: Region) -> dict:
    return {
        "key":         r.key,
        "label":       r.label,
        "description": r.description,
        "conditions": [
            {
                "station_id": c.station_id,
                "label":      c.label,
                "speed_min":  c.speed_min,
                "dir_low":    c.dir_low,
                "dir_high":   c.dir_high,
            }
            for c in r.conditions
        ],
    }


def _region_from_dict(d: dict) -> Region:
    return Region(
        key=d["key"],
        label=d["label"],
        description=d.get("description", ""),
        conditions=[
            Cond(
                station_id=c["station_id"],
                label=c["label"],
                speed_min=float(c["speed_min"]),
                dir_low=c.get("dir_low"),
                dir_high=c.get("dir_high"),
            )
            for c in d.get("conditions", [])
        ],
    )


def get_foehn_config_dict() -> dict:
    """Return the current (possibly overridden) config as a serialisable dict."""
    return {
        "regions": [_region_to_dict(r) for r in get_regions()],
        "pressure_pairs": [
            {
                "key":         p["key"],
                "south_id":    p["south_id"],
                "south_label": p["south_label"],
                "north_id":    p["north_id"],
                "north_label": p["north_label"],
                "threshold":   p["threshold"],
            }
            for p in get_pressure_pairs()
        ],
    }


def set_foehn_config(data: dict) -> None:
    """Apply a new config dict at runtime and persist it to JSON."""
    global _runtime_regions, _runtime_pressure_pairs
    regions = [_region_from_dict(r) for r in data.get("regions", [])]
    pairs   = [dict(p) for p in data.get("pressure_pairs", [])]
    _runtime_regions        = regions
    _runtime_pressure_pairs = pairs
    _FOEHN_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    _FOEHN_CONFIG_PATH.write_text(
        json.dumps(get_foehn_config_dict(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _logger.info("foehn_detection: config updated and saved to %s", _FOEHN_CONFIG_PATH)


def reset_foehn_config() -> None:
    """Remove the persisted override and revert to hardcoded defaults."""
    global _runtime_regions, _runtime_pressure_pairs
    _runtime_regions        = None
    _runtime_pressure_pairs = None
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
# Virtual stations written to InfluxDB by the FoehnCollector.
# station_id = "foehn-<region_key>".  Field: foehn_active (see STATUS_TO_NUMERIC).
# Pilots use these as rule-set condition stations with field="foehn_active".
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
        return "beliebig"
    lo = _DIR_NAMES.get(low,  f"{low:.0f}°")
    hi = _DIR_NAMES.get(high, f"{high:.0f}°")
    return f"{lo}–{hi}"


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def eval_cond(cond: Cond, latest: dict[str, dict]) -> dict:
    m = latest.get(cond.station_id)
    if m is None:
        return {
            "station_id":       cond.station_id,
            "label":            cond.label,
            "dir_desc":         dir_label(cond.dir_low, cond.dir_high),
            "speed_min":        cond.speed_min,
            "actual_direction": None,
            "actual_speed":     None,
            "actual_gust":      None,
            "data_available":   False,
            "speed_ok":         False,
            "dir_ok":           False,
            "met":              False,
            "timestamp":        None,
        }

    spd  = m.get("wind_speed")
    gust = m.get("wind_gust")
    dir_ = m.get("wind_direction")

    speed_ok = gust is not None and gust >= cond.speed_min
    if cond.dir_low is not None and cond.dir_high is not None:
        dir_ok = in_range(dir_, cond.dir_low, cond.dir_high)
    else:
        dir_ok = True

    return {
        "station_id":       cond.station_id,
        "label":            cond.label,
        "dir_desc":         dir_label(cond.dir_low, cond.dir_high),
        "speed_min":        cond.speed_min,
        "actual_direction": round(dir_, 1) if dir_ is not None else None,
        "actual_speed":     round(spd,  1) if spd  is not None else None,
        "actual_gust":      round(gust, 1) if gust is not None else None,
        "data_available":   True,
        "speed_ok":         speed_ok,
        "dir_ok":           dir_ok,
        "met":              speed_ok and dir_ok,
        "timestamp":        m["timestamp"].isoformat() if m.get("timestamp") else None,
    }


def eval_region(region: Region, latest: dict[str, dict]) -> dict:
    conds    = [eval_cond(c, latest) for c in region.conditions]
    total    = len(conds)
    met      = sum(1 for c in conds if c["met"])
    has_data = sum(1 for c in conds if c["data_available"])

    if met == total:
        status = "active"
    elif met > 0:
        status = "partial"
    elif has_data == 0:
        status = "no_data"
    else:
        status = "inactive"

    return {
        "key":              region.key,
        "label":            region.label,
        "description":      region.description,
        "status":           status,
        "conditions_met":   met,
        "conditions_total": total,
        "conditions":       conds,
    }


def build_all_pressures(latest: dict[str, dict]) -> list[dict]:
    """Evaluate all pressure pairs against live/forecast data."""
    result = []
    for pair in get_pressure_pairs():
        s_data = latest.get(pair["south_id"])
        n_data = latest.get(pair["north_id"])
        s_p    = s_data.get("pressure_qnh") if s_data else None
        n_p    = n_data.get("pressure_qnh") if n_data else None
        delta  = round(s_p - n_p, 2) if (s_p is not None and n_p is not None) else None
        result.append({
            "key":              pair["key"],
            "south_station_id": pair["south_id"],
            "north_station_id": pair["north_id"],
            "south_label":      pair["south_label"],
            "north_label":      pair["north_label"],
            "south_hpa":        round(s_p, 2) if s_p is not None else None,
            "north_hpa":        round(n_p, 2) if n_p is not None else None,
            "delta_hpa":        delta,
            "threshold_hpa":    pair["threshold"],
            "active":           delta is not None and delta >= pair["threshold"],
        })
    return result


_try_load_foehn_config()


def build_response(regions: list[dict], pressures: list[dict], assessed_at: str, extra: dict | None = None) -> dict:
    """Shared response builder for /status, /forecast, and /observation."""
    active_regions  = [r["key"] for r in regions if r["status"] == "active"]
    partial_regions = [r["key"] for r in regions if r["status"] == "partial"]
    pressure_risk   = any(p["active"] for p in pressures)
    if active_regions:
        overall_status = "active"
    elif pressure_risk:
        overall_status = "risk"
    elif partial_regions:
        overall_status = "partial"
    else:
        overall_status = "inactive"

    resp = {
        "assessed_at": assessed_at,
        "overall": {
            "status":          overall_status,
            "active_regions":  active_regions,
            "partial_regions": partial_regions,
            "pressure_risk":   pressure_risk,
        },
        "regions":   regions,
        "pressures": pressures,
    }
    if extra:
        resp.update(extra)
    return resp
