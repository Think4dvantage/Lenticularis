"""
Tests for the derived thermal-forecast metrics.

services/thermal.py is pure (no I/O), so no fixtures are needed.
"""
from __future__ import annotations

import pytest

from lenticularis.services.thermal import (
    blue_thermal,
    ceiling_spread_m,
    cin_effective,
    cloud_base_agl_m,
    overdevelopment_risk,
    thermal_ceiling_m,
    thermal_strength,
    turbulence_index,
)


# ---------------------------------------------------------------------------
# cin_effective
# ---------------------------------------------------------------------------

def test_cin_effective_none_becomes_zero():
    assert cin_effective(None) == 0.0


def test_cin_effective_passes_through_real_value():
    assert cin_effective(-150.0) == -150.0


# ---------------------------------------------------------------------------
# thermal_ceiling_m
# ---------------------------------------------------------------------------

def test_thermal_ceiling_lower_of_the_two():
    assert thermal_ceiling_m(2000.0, 3500.0) == 2000.0
    assert thermal_ceiling_m(4000.0, 3500.0) == 3500.0


def test_thermal_ceiling_one_input_null():
    assert thermal_ceiling_m(None, 3500.0) == 3500.0
    assert thermal_ceiling_m(2000.0, None) == 2000.0


def test_thermal_ceiling_both_null():
    assert thermal_ceiling_m(None, None) is None


# ---------------------------------------------------------------------------
# cloud_base_agl_m
# ---------------------------------------------------------------------------

def test_cloud_base_agl_normal():
    assert cloud_base_agl_m(2000.0, 800.0) == 1200.0


def test_cloud_base_agl_clamps_at_zero():
    assert cloud_base_agl_m(500.0, 800.0) == 0.0


def test_cloud_base_agl_null_inputs():
    assert cloud_base_agl_m(None, 800.0) is None
    assert cloud_base_agl_m(2000.0, None) is None


# ---------------------------------------------------------------------------
# thermal_strength
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "solar,expected_base",
    [
        (0, 0), (149, 0),
        (150, 1), (299, 1),
        (300, 2), (499, 2),
        (500, 3), (699, 3),
        (700, 4), (849, 4),
        (850, 5), (2000, 5),
    ],
)
def test_thermal_strength_solar_bands(solar, expected_base):
    result = thermal_strength(solar, cape=None, cloud_cover=None, cin=None, sunshine=None)
    assert result == expected_base


def test_thermal_strength_null_solar_is_null():
    assert thermal_strength(None, cape=1000, cloud_cover=0, cin=None, sunshine=60) is None


def test_thermal_strength_cape_bonus():
    without = thermal_strength(500, cape=299, cloud_cover=None, cin=None, sunshine=None)
    with_bonus = thermal_strength(500, cape=300, cloud_cover=None, cin=None, sunshine=None)
    assert with_bonus == without + 1


def test_thermal_strength_cape_none_no_bonus_no_crash():
    result = thermal_strength(500, cape=None, cloud_cover=None, cin=None, sunshine=None)
    assert result == 3  # base score for 500, no bonus applied


def test_thermal_strength_cloud_cover_penalty():
    base = thermal_strength(850, cape=None, cloud_cover=None, cin=None, sunshine=None)
    light_penalty = thermal_strength(850, cape=None, cloud_cover=71, cin=None, sunshine=None)
    heavy_penalty = thermal_strength(850, cape=None, cloud_cover=91, cin=None, sunshine=None)
    assert light_penalty == base - 1
    assert heavy_penalty == base - 2


def test_thermal_strength_cloud_cover_at_threshold_no_penalty():
    base = thermal_strength(850, cape=None, cloud_cover=None, cin=None, sunshine=None)
    at_threshold = thermal_strength(850, cape=None, cloud_cover=70, cin=None, sunshine=None)
    assert at_threshold == base


def test_thermal_strength_cin_penalty():
    base = thermal_strength(500, cape=None, cloud_cover=None, cin=None, sunshine=None)
    capped = thermal_strength(500, cape=None, cloud_cover=None, cin=-100, sunshine=None)
    assert capped == base - 1


def test_thermal_strength_cin_none_is_not_a_cap():
    # cin=None means NO inhibition layer — must not be treated as a strong cap
    with_none = thermal_strength(500, cape=None, cloud_cover=None, cin=None, sunshine=None)
    with_weak_cin = thermal_strength(500, cape=None, cloud_cover=None, cin=-10, sunshine=None)
    assert with_none == with_weak_cin  # neither triggers the -100 penalty


def test_thermal_strength_sunshine_guard_fires_when_cloud_not_already_penalising():
    base = thermal_strength(500, cape=None, cloud_cover=40, cin=None, sunshine=None)
    patchy = thermal_strength(500, cape=None, cloud_cover=40, cin=None, sunshine=20)
    assert patchy == base - 1


def test_thermal_strength_sunshine_guard_suppressed_when_cloud_already_penalised():
    # cloud_cover already fired a penalty — sunshine must not double-count the same sky
    cloudy_no_sun_info = thermal_strength(500, cape=None, cloud_cover=85, cin=None, sunshine=None)
    cloudy_with_patchy_sun = thermal_strength(500, cape=None, cloud_cover=85, cin=None, sunshine=20)
    assert cloudy_with_patchy_sun == cloudy_no_sun_info


def test_thermal_strength_sunshine_none_no_penalty():
    base = thermal_strength(500, cape=None, cloud_cover=40, cin=None, sunshine=None)
    explicit_high_sun = thermal_strength(500, cape=None, cloud_cover=40, cin=None, sunshine=45)
    assert base == explicit_high_sun


def test_thermal_strength_clamped_at_zero_stacked_penalties():
    result = thermal_strength(0, cape=None, cloud_cover=95, cin=-200, sunshine=5)
    assert result == 0


def test_thermal_strength_clamped_at_five_stacked_bonuses():
    result = thermal_strength(1000, cape=1000, cloud_cover=0, cin=None, sunshine=60)
    assert result == 5


# ---------------------------------------------------------------------------
# overdevelopment_risk
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "cape,expected_base",
    [
        (0, 0), (299, 0),
        (300, 1), (799, 1),
        (800, 2), (1499, 2),
        (1500, 3), (3000, 3),
    ],
)
def test_overdevelopment_risk_cape_bands(cape, expected_base):
    # Keep cin capped (<=-25) and cloud_mid low so only the base band shows.
    result = overdevelopment_risk(cape, cin=-100, cloud_mid=0)
    assert result == expected_base


def test_overdevelopment_risk_null_cape_is_null():
    assert overdevelopment_risk(None, cin=None, cloud_mid=None) is None


def test_overdevelopment_risk_uncapped_cin_bonus():
    capped = overdevelopment_risk(500, cin=-100, cloud_mid=0)
    uncapped = overdevelopment_risk(500, cin=-24, cloud_mid=0)
    assert uncapped == capped + 1


def test_overdevelopment_risk_uncapped_requires_cape_threshold():
    # cape below the bonus threshold — no cin bonus even if cin is weak
    result = overdevelopment_risk(200, cin=0, cloud_mid=0)
    assert result == 0


def test_overdevelopment_risk_cloud_mid_gate_requires_cape():
    # Stable day (low cape) with heavy mid cloud — no bump, it's just shade
    result = overdevelopment_risk(100, cin=-100, cloud_mid=90)
    assert result == 0


def test_overdevelopment_risk_cloud_mid_bumps_with_cape_present():
    without_mid = overdevelopment_risk(500, cin=-100, cloud_mid=0)
    with_mid = overdevelopment_risk(500, cin=-100, cloud_mid=90)
    assert with_mid == without_mid + 1


def test_overdevelopment_risk_cloud_mid_none_no_bump():
    result = overdevelopment_risk(500, cin=-100, cloud_mid=None)
    assert result == overdevelopment_risk(500, cin=-100, cloud_mid=0)


def test_overdevelopment_risk_clamped_at_three():
    result = overdevelopment_risk(3000, cin=100, cloud_mid=90)
    assert result == 3


# ---------------------------------------------------------------------------
# blue_thermal
# ---------------------------------------------------------------------------

def test_blue_thermal_large_gap_is_one():
    assert blue_thermal(lfc=3000.0, lcl=2000.0) == 1


def test_blue_thermal_small_gap_is_zero():
    assert blue_thermal(lfc=2400.0, lcl=2000.0) == 0


def test_blue_thermal_at_threshold_is_zero():
    # gap must be strictly > 500, not >=
    assert blue_thermal(lfc=2500.0, lcl=2000.0) == 0


def test_blue_thermal_null_inputs():
    assert blue_thermal(None, 2000.0) is None
    assert blue_thermal(2500.0, None) is None


# ---------------------------------------------------------------------------
# turbulence_index
# ---------------------------------------------------------------------------

@pytest.mark.parametrize(
    "tke,expected",
    [(0.0, 0), (0.99, 0), (1.0, 1), (1.99, 1), (2.0, 2), (4.99, 2), (5.0, 3), (10.0, 3)],
)
def test_turbulence_index_bands(tke, expected):
    assert turbulence_index(tke) == expected


def test_turbulence_index_null():
    assert turbulence_index(None) is None


# ---------------------------------------------------------------------------
# ceiling_spread_m
# ---------------------------------------------------------------------------

def test_ceiling_spread_normal():
    assert ceiling_spread_m(2100.0, 2600.0) == 500.0


def test_ceiling_spread_null_inputs():
    assert ceiling_spread_m(None, 2600.0) is None
    assert ceiling_spread_m(2100.0, None) is None


def test_ceiling_spread_zero_distinct_from_none():
    result = ceiling_spread_m(2000.0, 2000.0)
    assert result == 0.0
    assert result is not None
