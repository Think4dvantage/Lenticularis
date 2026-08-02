"""
Derived thermal-forecast metrics.

Pure functions over the raw lsmfapi thermal-grid fields (see
``.ai/context/lsmfapi-thermal-grid.md`` and ``specs/006-thermal-forecast/plan.md`` §6).
No I/O, no InfluxDB, no Pydantic — plain floats/ints in, plain floats/ints (or ``None``) out,
so they are unit-testable without any fixture and tunable by editing the constants below.

``cin`` is the one field whose ``None`` does not mean "missing" — it means "no convective
inhibition layer present" (ICON fill value clipped by lsmfapi). Every function below that uses
``cin`` takes the caller-computed ``cin_effective`` (``0.0`` when ``cin`` is ``None``), never
``cin`` itself, so that substitution happens in exactly one place.
"""

from __future__ import annotations

from typing import Optional

# --- thermal_strength ------------------------------------------------------
THERMAL_SOLAR_BANDS = ((150, 0), (300, 1), (500, 2), (700, 3), (850, 4))  # W/m² → base score
THERMAL_CAPE_BONUS_JKG = 300  # cape at/above this → +1
THERMAL_CLOUD_PENALTY_PCT = 70  # cloud_cover above this → -1
THERMAL_CLOUD_HEAVY_PCT = 90  # cloud_cover above this → -2 (instead of -1)
THERMAL_CIN_PENALTY_JKG = -100  # cin_effective at/below this → -1
THERMAL_SUNSHINE_PATCHY_MIN = 30  # min/h — sun out less than half the hour → -1

# --- overdevelopment_risk ---------------------------------------------------
OVERDEV_CAPE_BANDS = ((300, 0), (800, 1), (1500, 2))  # J/kg → risk band
OVERDEV_UNCAPPED_CIN = -25  # cin_effective above (weaker than) this = convection fires freely
OVERDEV_CLOUD_MID_PCT = 50  # mid-level cloud above this, with CAPE present → +1

# --- blue_thermal ------------------------------------------------------------
BLUE_THERMAL_LFC_LCL_GAP_M = 500

# --- turbulence_index --------------------------------------------------------
TKE_BANDS = ((1.0, 0), (2.0, 1), (5.0, 2))  # J/kg → index


def cin_effective(cin: Optional[float]) -> float:
    """``None`` means no inhibition layer at all — substitute 0.0, never treat as a cap."""
    return 0.0 if cin is None else cin


def thermal_ceiling_m(lcl: Optional[float], freezing_level: Optional[float]) -> Optional[float]:
    """Usable thermal ceiling: cloud base caps it, the freezing level is the hard ceiling."""
    if lcl is None and freezing_level is None:
        return None
    if lcl is None:
        return freezing_level
    if freezing_level is None:
        return lcl
    return min(lcl, freezing_level)


def cloud_base_agl_m(lcl: Optional[float], elevation_m: Optional[float]) -> Optional[float]:
    """Cloud base above ground at a station. Clamped at 0 — an LCL below terrain means cloud on the deck."""
    if lcl is None or elevation_m is None:
        return None
    return max(0.0, lcl - elevation_m)


def _band_score(value: float, bands: tuple) -> int:
    """Highest band whose threshold ``value`` is still below; ``bands[-1]``'s score if at/above all."""
    for threshold, score in bands:
        if value < threshold:
            return score
    return bands[-1][1] + 1


def thermal_strength(
    solar: Optional[float],
    cape: Optional[float],
    cloud_cover: Optional[float],
    cin: Optional[float],
    sunshine: Optional[float],
) -> Optional[int]:
    """0-5 index. ``solar`` is the trigger — missing it makes the whole index null.

    A missing modifier (``cape``, ``cloud_cover``, ``sunshine``) contributes nothing.
    """
    if solar is None:
        return None

    score = _band_score(solar, THERMAL_SOLAR_BANDS)

    if cape is not None and cape >= THERMAL_CAPE_BONUS_JKG:
        score += 1

    if cloud_cover is not None:
        if cloud_cover > THERMAL_CLOUD_HEAVY_PCT:
            score -= 2
        elif cloud_cover > THERMAL_CLOUD_PENALTY_PCT:
            score -= 1

    if cin_effective(cin) <= THERMAL_CIN_PENALTY_JKG:
        score -= 1

    # Guarded on cloud_cover so patchy-sun does not double-count an overcast sky the
    # cloud_cover term already penalised — see plan.md §6.3.
    if (
        sunshine is not None
        and sunshine < THERMAL_SUNSHINE_PATCHY_MIN
        and (cloud_cover is None or cloud_cover <= THERMAL_CLOUD_PENALTY_PCT)
    ):
        score -= 1

    return max(0, min(5, score))


def overdevelopment_risk(
    cape: Optional[float],
    cin: Optional[float],
    cloud_mid: Optional[float],
) -> Optional[int]:
    """0-3 index. ``None`` when ``cape`` is ``None`` — the index is meaningless without it."""
    if cape is None:
        return None

    risk = _band_score(cape, OVERDEV_CAPE_BANDS)

    if cape >= THERMAL_CAPE_BONUS_JKG and cin_effective(cin) > OVERDEV_UNCAPPED_CIN:
        risk += 1

    if cape >= THERMAL_CAPE_BONUS_JKG and cloud_mid is not None and cloud_mid > OVERDEV_CLOUD_MID_PCT:
        risk += 1

    return max(0, min(3, risk))


def blue_thermal(lfc: Optional[float], lcl: Optional[float]) -> Optional[int]:
    """1 when the level of free convection is well above cloud base ("blue thermals")."""
    if lfc is None or lcl is None:
        return None
    return 1 if (lfc - lcl) > BLUE_THERMAL_LFC_LCL_GAP_M else 0


def turbulence_index(tke: Optional[float]) -> Optional[int]:
    """0-3 index; 2+ is rough air."""
    if tke is None:
        return None
    return _band_score(tke, TKE_BANDS)


def ceiling_spread_m(lcl_min: Optional[float], lcl_max: Optional[float]) -> Optional[float]:
    """Ensemble confidence in the cloud-base forecast. Raw metres, not a band."""
    if lcl_min is None or lcl_max is None:
        return None
    return lcl_max - lcl_min
