"""
Rule set evaluator for Lenticularis.

Walks a rule set's condition tree, fetches the latest measurement per station
from InfluxDB, evaluates each condition/group, applies combination logic and
returns a structured result dict.

AND groups
----------
Conditions sharing the same ``group_id`` (non-NULL) are ANDed together.
The group produces a result colour only when ALL conditions in the group match.
The result colour chosen for the group is the *worst* colour among the matching
conditions.

Standalone conditions (``group_id`` = NULL) are evaluated independently.

Combination logic
-----------------
``worst_wins``   — the worst (most restrictive) triggered colour becomes the decision.
``majority_vote`` — the colour with the most votes wins.

If no conditions trigger (either because none matched or no data), the decision
defaults to ``"green"`` (benefit of the doubt).

Direction ranges
----------------
``in_direction_range`` uses clockwise arc semantics:
  - (90°, 135°)  → covers 90–135°  (E to SE, short arc)
  - (270°, 90°)  → covers 270–360°–0°–90° (W to E through N, wraps)
  - (135°, 90°)  → covers 135–360°–0°–90° (SE to E the long way)

Compass shorthand degrees used by the editor:
  N=0  NNE=22.5  NE=45  ENE=67.5  E=90  ESE=112.5  SE=135  SSE=157.5
  S=180  SSW=202.5  SW=225  WSW=247.5  W=270  WNW=292.5  NW=315  NNW=337.5

InfluxDB field mapping
----------------------
Condition field     → InfluxDB field
wind_speed          → wind_speed
wind_gust           → wind_gust
wind_direction      → wind_direction
temperature         → temperature
humidity            → humidity
pressure            → pressure_qnh   (default to QNH)
pressure_delta      → pressure_qnh   (delta between two stations, QNH-based)
precipitation       → precipitation
snow_depth          → snow_depth
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from typing import Optional

from lenticularis.database.influx import InfluxClient
from lenticularis.database.models import RuleCondition, RuleSet

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

FIELD_MAP: dict[str, str] = {
    "wind_speed":     "wind_speed",
    "wind_gust":      "wind_gust",
    "wind_direction": "wind_direction",
    "temperature":    "temperature",
    "humidity":       "humidity",
    "pressure":       "pressure_qnh",
    "pressure_delta": "pressure_qnh",
    "precipitation":  "precipitation",
    "snow_depth":     "snow_depth",
    # Föhn virtual stations (station_id = "foehn-<region>", e.g. "foehn-haslital").
    # Active=1.0, partial=0.5, inactive=0.0, no_data=-1.0.
    "foehn_active":   "foehn_active",
}

COLOUR_RANK: dict[str, int] = {"green": 0, "orange": 1, "red": 2}


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _worst(colours: list[str]) -> str:
    """Return the most restrictive colour from a list."""
    return max(colours, key=lambda c: COLOUR_RANK.get(c, 0))


def _in_direction_range(actual: float, start: float, end: float) -> bool:
    """
    Return True if compass direction ``actual`` lies within the clockwise arc
    from ``start`` to ``end`` (all degrees, 0–360).

    Handles wrap-through-north automatically: if ``start > end`` (mod 360)
    the arc crosses 0°.
    """
    a = actual % 360
    s = start % 360
    e = end % 360
    if s <= e:
        return s <= a <= e
    # Wraps through 0° (e.g., 315 → 45 covers 315–360 and 0–45)
    return a >= s or a <= e


def _match(
    actual: float, operator: str, value_a: float, value_b: Optional[float]
) -> bool:
    """Apply one operator comparison; returns True when the condition is met."""
    if operator == ">":
        return actual > value_a
    if operator == ">=":
        return actual >= value_a
    if operator == "<":
        return actual < value_a
    if operator == "<=":
        return actual <= value_a
    if operator == "=":
        return abs(actual - value_a) < 0.001
    if operator in ("between", "not_between"):
        if value_b is None:
            return False
        inside = value_a <= actual <= value_b
        return inside if operator == "between" else not inside
    if operator == "in_direction_range":
        if value_b is None:
            return False
        return _in_direction_range(actual, value_a, value_b)
    logger.warning("Unknown condition operator: %s", operator)
    return False


def _eval_condition(
    cond: RuleCondition,
    station_data: dict[str, dict],
) -> tuple[bool, Optional[float]]:
    """
    Evaluate a single RuleCondition against pre-fetched station data.

    Returns ``(matched: bool, actual_value: float | None)``.
    Returns ``(False, None)`` when data is unavailable for the station.
    """
    data = station_data.get(cond.station_id)
    if data is None:
        return False, None

    influx_field = FIELD_MAP.get(cond.field)
    if not influx_field:
        logger.warning("Unknown condition field: %s", cond.field)
        return False, None

    if cond.field == "pressure_delta":
        # Compare QNH pressure between two stations
        data_b = station_data.get(cond.station_b_id or "")
        if data_b is None:
            return False, None
        raw_a = data.get(influx_field)
        raw_b = data_b.get(influx_field)
        if raw_a is None or raw_b is None:
            return False, None
        actual = float(raw_a) - float(raw_b)
    else:
        raw = data.get(influx_field)
        if raw is None:
            return False, None
        actual = float(raw)

    matched = _match(actual, cond.operator, cond.value_a, cond.value_b)
    return matched, actual


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def run_evaluation(ruleset: RuleSet, influx: InfluxClient) -> dict:
    """
    Evaluate all conditions in *ruleset* against current InfluxDB data.

    Returns a dict:
    ::

        {
            "decision":          "green" | "orange" | "red",
            "evaluated_at":      ISO8601 string,
            "condition_results": [
                {
                    "condition_id":      str,
                    "station_id":        str,
                    "station_b_id":      str | None,
                    "field":             str,
                    "actual_value":      float | None,
                    "matched":           bool,
                    "result_colour":     str,
                    "group_id":          str | None,
                    "group_all_matched": bool | None,   # only for grouped conditions
                }
            ],
            "no_data_stations":   [str, ...],
        }

    The *decision* is always ``"green"`` when no conditions trigger (no data /
    all conditions false) — this is intentional: unknown = benefit of the doubt.
    Consumers should surface the ``no_data_stations`` list to the user.
    """
    conditions: list[RuleCondition] = ruleset.conditions

    # ---- collect unique station IDs ----------------------------------------
    station_ids: set[str] = set()
    for c in conditions:
        station_ids.add(c.station_id)
        if c.station_b_id:
            station_ids.add(c.station_b_id)

    # ---- fetch latest data per station -------------------------------------
    station_data: dict[str, dict] = {}
    no_data: list[str] = []
    for sid in station_ids:
        d = influx.query_latest(sid)
        if d:
            station_data[sid] = d
        else:
            no_data.append(sid)
            logger.warning("No InfluxDB data for station %s during evaluation of ruleset %s", sid, ruleset.id)

    # ---- partition into standalone vs AND groups ---------------------------
    groups: dict[str, list[RuleCondition]] = {}
    standalone: list[RuleCondition] = []
    for c in conditions:
        if c.group_id:
            groups.setdefault(c.group_id, []).append(c)
        else:
            standalone.append(c)

    condition_results: list[dict] = []
    triggered_colours: list[str] = []

    # ---- evaluate standalone conditions ------------------------------------
    for cond in standalone:
        matched, actual = _eval_condition(cond, station_data)
        condition_results.append({
            "condition_id":  cond.id,
            "station_id":    cond.station_id,
            "station_b_id":  cond.station_b_id,
            "field":         cond.field,
            "operator":      cond.operator,
            "value_a":       cond.value_a,
            "value_b":       cond.value_b,
            "actual_value":  actual,
            "matched":       matched,
            "result_colour": cond.result_colour,
            "group_id":      None,
            "group_all_matched": None,
        })
        if matched:
            triggered_colours.append(cond.result_colour)

    # ---- evaluate AND groups -----------------------------------------------
    for group_id, group_conds in groups.items():
        evals: list[tuple[bool, Optional[float]]] = [
            _eval_condition(c, station_data) for c in group_conds
        ]
        all_matched = all(m for m, _ in evals)

        for (matched, actual), cond in zip(evals, group_conds):
            condition_results.append({
                "condition_id":      cond.id,
                "station_id":        cond.station_id,
                "station_b_id":      cond.station_b_id,
                "field":             cond.field,
                "operator":          cond.operator,
                "value_a":           cond.value_a,
                "value_b":           cond.value_b,
                "actual_value":      actual,
                "matched":           matched,
                "result_colour":     cond.result_colour,
                "group_id":          group_id,
                "group_all_matched": all_matched,
            })

        if all_matched:
            group_colour = _worst([c.result_colour for c in group_conds])
            triggered_colours.append(group_colour)

    # ---- apply combination logic -------------------------------------------
    if not triggered_colours:
        decision = "green"
    elif ruleset.combination_logic == "worst_wins":
        decision = _worst(triggered_colours)
    else:  # majority_vote
        counter: dict[str, int] = {"green": 0, "orange": 0, "red": 0}
        for colour in triggered_colours:
            counter[colour] = counter.get(colour, 0) + 1
        # Tiebreak: prefer more restrictive colour
        decision = max(counter, key=lambda k: (counter[k], COLOUR_RANK[k]))

    return {
        "decision":          decision,
        "evaluated_at":      datetime.now(timezone.utc).isoformat(),
        "condition_results": condition_results,
        "no_data_stations":  no_data,
    }


def run_forecast_evaluation(
    ruleset: RuleSet,
    influx: InfluxClient,
    horizon_hours: int = 120,
    lat: Optional[float] = None,
    lon: Optional[float] = None,
    active_window_hours: float = 1.0,
) -> list[dict]:
    """
    Evaluate ruleset conditions against forecast data for the next ``horizon_hours``.

    Reuses the identical condition/group/combination logic as ``run_evaluation``
    but iterates over hourly ``valid_time`` steps from the ``weather_forecast``
    InfluxDB measurement instead of fetching the single latest ``weather_data`` value.

    Returns a time-ordered list of forecast steps::

        [
            {
                "valid_time":        ISO8601 string,
                "decision":          "green" | "orange" | "red",
                "condition_results": [...],
            },
            ...
        ]

    Does **not** write to InfluxDB — results are ephemeral and returned on demand.
    Returns an empty list when no forecast data is available yet.

    When *lat* and *lon* are provided, each step is annotated with
    ``in_active_window`` (±*active_window_hours* around sunrise).
    """
    from lenticularis.utils.sunrise import is_in_active_window as _in_window

    conditions: list[RuleCondition] = ruleset.conditions
    if not conditions:
        return []

    station_ids: list[str] = list(
        {c.station_id for c in conditions}
        | {c.station_b_id for c in conditions if c.station_b_id}
    )

    forecast_by_station = influx.query_forecast_for_stations(station_ids, horizon_hours)
    if not forecast_by_station:
        return []

    all_valid_times: set[str] = set()
    for vt_map in forecast_by_station.values():
        all_valid_times.update(vt_map.keys())
    sorted_valid_times = sorted(all_valid_times)

    groups: dict[str, list[RuleCondition]] = {}
    standalone: list[RuleCondition] = []
    for c in conditions:
        if c.group_id:
            groups.setdefault(c.group_id, []).append(c)
        else:
            standalone.append(c)

    steps: list[dict] = []
    for vt_iso in sorted_valid_times:
        station_data: dict[str, dict] = {
            sid: vt_map[vt_iso]
            for sid, vt_map in forecast_by_station.items()
            if vt_iso in vt_map
        }

        condition_results: list[dict] = []
        triggered_colours: list[str] = []

        for cond in standalone:
            matched, actual = _eval_condition(cond, station_data)
            condition_results.append({
                "condition_id":      cond.id,
                "station_id":        cond.station_id,
                "station_b_id":      cond.station_b_id,
                "field":             cond.field,
                "operator":          cond.operator,
                "value_a":           cond.value_a,
                "value_b":           cond.value_b,
                "actual_value":      actual,
                "matched":           matched,
                "result_colour":     cond.result_colour,
                "group_id":          None,
                "group_all_matched": None,
            })
            if matched:
                triggered_colours.append(cond.result_colour)

        for group_id, group_conds in groups.items():
            evals = [_eval_condition(c, station_data) for c in group_conds]
            all_matched = all(m for m, _ in evals)
            for (matched, actual), cond in zip(evals, group_conds):
                condition_results.append({
                    "condition_id":      cond.id,
                    "station_id":        cond.station_id,
                    "station_b_id":      cond.station_b_id,
                    "field":             cond.field,
                    "operator":          cond.operator,
                    "value_a":           cond.value_a,
                    "value_b":           cond.value_b,
                    "actual_value":      actual,
                    "matched":           matched,
                    "result_colour":     cond.result_colour,
                    "group_id":          group_id,
                    "group_all_matched": all_matched,
                })
            if all_matched:
                triggered_colours.append(_worst([c.result_colour for c in group_conds]))

        if not triggered_colours:
            decision = "green"
        elif ruleset.combination_logic == "worst_wins":
            decision = _worst(triggered_colours)
        else:
            counter: dict[str, int] = {"green": 0, "orange": 0, "red": 0}
            for colour in triggered_colours:
                counter[colour] = counter.get(colour, 0) + 1
            decision = max(counter, key=lambda k: (counter[k], COLOUR_RANK[k]))

        step: dict = {
            "valid_time":        vt_iso,
            "decision":          decision,
            "condition_results": condition_results,
        }
        if lat is not None and lon is not None:
            try:
                vt_dt = datetime.fromisoformat(vt_iso)
            except ValueError:
                vt_dt = None
            step["in_active_window"] = (
                _in_window(vt_dt, lat, lon, active_window_hours)
                if vt_dt is not None else True
            )
        else:
            step["in_active_window"] = True
        steps.append(step)

    return steps


def write_decision(ruleset: RuleSet, result: dict, influx: InfluxClient) -> None:
    """
    Persist an evaluation result to the ``rule_decisions`` InfluxDB measurement.

    Tags:   ``ruleset_id``, ``owner_id``, ``site_type``
    Fields: ``decision`` (str), ``condition_results`` (JSON string)
    """
    from influxdb_client import Point  # local import to keep top-level imports tidy
    try:
        p = (
            Point("rule_decisions")
            .tag("ruleset_id", ruleset.id)
            .tag("owner_id", ruleset.owner_id)
            .tag("site_type", ruleset.site_type)
            .field("decision", result["decision"])
            .field("condition_results", json.dumps(result["condition_results"]))
        )
        influx._write_api.write(
            bucket=influx._cfg.bucket,
            org=influx._cfg.org,
            record=p,
        )
        logger.debug(
            "Wrote rule_decision for ruleset %s → %s", ruleset.id, result["decision"]
        )
    except Exception as exc:
        logger.error(
            "Failed to write rule_decisions for ruleset %s: %s", ruleset.id, exc
        )
