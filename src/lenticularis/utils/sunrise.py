"""
Sunrise/sunset calculation using the NOAA simplified solar algorithm.

Accuracy: ±5 minutes — sufficient for defining active daylight windows.
No external dependencies; pure Python math.

Usage::

    from lenticularis.utils.sunrise import is_in_active_window

    # True between (sunrise - 1h) and (sunset + 1h)
    active = is_in_active_window(datetime.now(timezone.utc), lat=47.0, lon=8.5)
"""
from __future__ import annotations

import math
from datetime import date, datetime, timedelta, timezone
from typing import Optional


def _solar_params(d: date) -> tuple[float, float]:
    """Return (equation_of_time_minutes, solar_declination_radians) for date *d*."""
    doy = d.timetuple().tm_yday
    gamma = 2 * math.pi / 365 * (doy - 1 + 6 / 24)

    eot = 229.18 * (
        0.000075
        + 0.001868 * math.cos(gamma)
        - 0.032077 * math.sin(gamma)
        - 0.014615 * math.cos(2 * gamma)
        - 0.04089  * math.sin(2 * gamma)
    )
    decl = (
        0.006918
        - 0.399912 * math.cos(gamma)
        + 0.070257 * math.sin(gamma)
        - 0.006758 * math.cos(2 * gamma)
        + 0.000907 * math.sin(2 * gamma)
        - 0.002697 * math.cos(3 * gamma)
        + 0.001480 * math.sin(3 * gamma)
    )
    return eot, decl


def _event_minutes_utc(d: date, lat: float, lon: float, is_sunset: bool) -> Optional[float]:
    """
    Return sunrise or sunset as minutes-since-midnight UTC.
    Returns None for polar regions with no event on that day.
    """
    eot, decl = _solar_params(d)
    lat_r = math.radians(lat)

    # 90.833° accounts for atmospheric refraction + solar disc radius
    cos_ha = (
        math.cos(math.radians(90.833)) / (math.cos(lat_r) * math.cos(decl))
        - math.tan(lat_r) * math.tan(decl)
    )
    if abs(cos_ha) > 1:
        return None  # polar day or polar night

    ha = math.degrees(math.acos(cos_ha))
    if is_sunset:
        ha = -ha  # sunset uses negative hour angle
    return (720 - 4 * (lon + ha) - eot) % 1440


def sunrise_utc(d: date, lat: float, lon: float) -> Optional[datetime]:
    """Return sunrise as a UTC-aware datetime. None for polar regions."""
    mins = _event_minutes_utc(d, lat, lon, is_sunset=False)
    if mins is None:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(seconds=int(mins * 60))


def sunset_utc(d: date, lat: float, lon: float) -> Optional[datetime]:
    """Return sunset as a UTC-aware datetime. None for polar regions."""
    mins = _event_minutes_utc(d, lat, lon, is_sunset=True)
    if mins is None:
        return None
    return datetime(d.year, d.month, d.day, tzinfo=timezone.utc) + timedelta(seconds=int(mins * 60))


def is_in_active_window(
    dt_utc: datetime,
    lat: float,
    lon: float,
    buffer_hours: float = 1.0,
) -> bool:
    """
    Return True if *dt_utc* falls between (sunrise − buffer_hours) and
    (sunset + buffer_hours) for the relevant calendar date.

    Checks the current date and adjacent dates so evaluations near midnight
    are handled correctly. Always returns True for polar regions.
    """
    for delta in (-1, 0, 1):
        d = (dt_utc + timedelta(days=delta)).date()
        sr = sunrise_utc(d, lat, lon)
        ss = sunset_utc(d, lat, lon)
        if sr is None or ss is None:
            return True  # polar day — always active
        window_start = sr - timedelta(hours=buffer_hours)
        window_end   = ss + timedelta(hours=buffer_hours)
        if window_start <= dt_utc <= window_end:
            return True
    return False
