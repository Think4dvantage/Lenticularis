"""
Shared pure helpers for weather data collectors.

Import these instead of reimplementing locally where possible.
Collectors with format-specific quirks (fga: strips "°", windline: handles "-")
keep their own local versions — partial de-duplication is intentional.
"""
from __future__ import annotations

from typing import Optional


def to_float(val: object) -> Optional[float]:
    """Tolerant numeric parse: return float or None on any non-numeric input."""
    if val is None:
        return None
    try:
        return float(val)
    except (TypeError, ValueError):
        return None


def normalize_wind_dir(val: object) -> Optional[int]:
    """Parse a wind direction to a [0, 359] integer, None-safe.

    Applies ``% 360`` so values like 360 normalise to 0.
    """
    v = to_float(val)
    if v is None:
        return None
    return int(v) % 360
