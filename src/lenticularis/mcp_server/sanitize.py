"""
Output hygiene for the public MCP tools: field allowlist, units, suppression of known-faulty
values, staleness, station-id validation and accent-insensitive text matching.
"""
from __future__ import annotations

import re
import unicodedata
from datetime import datetime, timezone
from typing import Any, Optional

STATION_ID_RE = re.compile(r"^[\w\-]{1,64}$")

# Observation fields exposed to callers (measured values only).
OBSERVATION_FIELDS: tuple[str, ...] = (
    "wind_speed", "wind_gust", "wind_direction", "temperature", "humidity",
    "pressure_qfe", "pressure_qff", "precipitation", "snow_depth",
)

# Forecast fields: the central value plus the ensemble spread where present.
FORECAST_FIELDS: tuple[str, ...] = (
    "wind_speed", "wind_gust", "wind_direction", "temperature", "humidity",
    "pressure_qff", "precipitation",
)
_SPREAD = ("_min", "_max")

UNITS: dict[str, str] = {
    "wind_speed": "km/h",
    "wind_gust": "km/h",
    "wind_direction": "degrees (meteorological, 0-360, direction the wind blows FROM)",
    "temperature": "°C",
    "humidity": "% relative",
    "pressure_qfe": "hPa (station pressure)",
    "pressure_qff": "hPa (sea-level pressure)",
    "precipitation": "mm",
    "snow_depth": "cm",
}

# Known-faulty sensor values that must never be exposed (spec FR-007).
# jfb-hollandiahutte-sac declares 3248 m but reports a ~750 m reading (~928 hPa / 23 °C).
SUPPRESSED_FIELDS: dict[str, frozenset[str]] = {
    "jfb-hollandiahutte-sac": frozenset({"temperature", "humidity", "pressure_qfe", "pressure_qff"}),
}


def is_valid_station_id(station_id: str) -> bool:
    return isinstance(station_id, str) and STATION_ID_RE.match(station_id) is not None


def _allowed(field: str, base_fields: tuple[str, ...]) -> bool:
    if field in base_fields:
        return True
    return any(field == f"{b}{s}" for b in base_fields for s in _SPREAD)


def base_field(field: str) -> str:
    for s in _SPREAD:
        if field.endswith(s):
            return field[: -len(s)]
    return field


def suppressed_for(station_ids: str | list[str]) -> frozenset[str]:
    """Union of suppressed fields over one station or a whole merged group's members."""
    ids = [station_ids] if isinstance(station_ids, str) else station_ids
    out: set[str] = set()
    for sid in ids:
        out |= SUPPRESSED_FIELDS.get(sid, frozenset())
    return frozenset(out)


def clean_values(
    values: dict[str, Any],
    station_id: str | list[str],
    base_fields: tuple[str, ...] = OBSERVATION_FIELDS,
) -> dict[str, Any]:
    """Keep only allowlisted, non-null, non-suppressed numeric fields (rounded).

    ``station_id`` may be a list (a merged group's members): pooled values cannot be attributed to
    one member, so a field is dropped if ANY member has it suppressed.
    """
    suppressed = suppressed_for(station_id)
    out: dict[str, Any] = {}
    for k, v in values.items():
        if v is None or not _allowed(k, base_fields) or base_field(k) in suppressed:
            continue
        if isinstance(v, bool) or not isinstance(v, (int, float)):
            continue
        out[k] = round(float(v), 2) if isinstance(v, float) else v
    return out


def units_for(keys: list[str] | set[str]) -> dict[str, str]:
    return {k: UNITS[base_field(k)] for k in sorted(keys) if base_field(k) in UNITS}


def as_utc(ts: Any) -> Optional[datetime]:
    if ts is None:
        return None
    if isinstance(ts, str):
        try:
            ts = datetime.fromisoformat(ts.replace("Z", "+00:00"))
        except ValueError:
            return None
    if isinstance(ts, datetime):
        return ts.replace(tzinfo=timezone.utc) if ts.tzinfo is None else ts.astimezone(timezone.utc)
    return None


def iso_z(ts: Any) -> Optional[str]:
    dt = as_utc(ts)
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ") if dt else None


def age_minutes(ts: Any, now: Optional[datetime] = None) -> Optional[int]:
    dt = as_utc(ts)
    if dt is None:
        return None
    now = now or datetime.now(timezone.utc)
    return max(0, int((now - dt).total_seconds() // 60))


# ---------------------------------------------------------------------------
# Accent / umlaut-insensitive matching (Swiss names: Zürich, Säntis, Genève)
# ---------------------------------------------------------------------------

_GERMAN_EXPANSION = str.maketrans({"ä": "ae", "ö": "oe", "ü": "ue", "Ä": "ae", "Ö": "oe", "Ü": "ue", "ß": "ss"})


def _strip_marks(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text)
    return "".join(c for c in decomposed if not unicodedata.combining(c))


def fold_variants(text: str) -> tuple[str, str]:
    """Two normalised forms: German-expanded (ü→ue) and plain accent-stripped (ü→u)."""
    low = text.lower()
    return _strip_marks(low.translate(_GERMAN_EXPANSION)), _strip_marks(low)


def name_matches(query: str, name: str) -> bool:
    qa, qb = fold_variants(query.strip())
    if not qa:
        return True
    na, nb = fold_variants(name)
    return qa in na or qb in nb or qa in nb or qb in na
