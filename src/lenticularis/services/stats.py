"""
Ruleset statistics service.

All metrics are computed from the ``rule_decisions`` InfluxDB measurement.
Computation is done in Python after fetching raw decision rows — this keeps
Flux queries simple and avoids complex window/pivot operations.
"""

from __future__ import annotations

import json
import logging
from collections import defaultdict
from datetime import datetime, timezone

from lenticularis.database.influx import InfluxClient

logger = logging.getLogger(__name__)


def _fetch_decisions(influx: InfluxClient, ruleset_id: str, hours: int) -> list[dict]:
    return influx.query_decision_history(ruleset_id, hours)


# ---------------------------------------------------------------------------
# All-rulesets overview — single call returns everything the UI needs
# ---------------------------------------------------------------------------

def all_rulesets_overview(
    influx: InfluxClient,
    rulesets_info: list[dict],  # [{id, name, site_type}]
    hours: int = 720,
) -> dict:
    """
    Fetch decision history for all rulesets in one InfluxDB query and compute:
    - per_ruleset: [{id, name, site_type, green_pct, orange_pct, red_pct, total}]
    - aggregate: {green_pct, orange_pct, red_pct, total}
    - hourly_pattern: [{hour, green_pct, orange_pct, red_pct, total}] (aggregated)
    - flyable_days: [{date, green, orange, red}] (aggregated across all rulesets)
    """
    ids = [r["id"] for r in rulesets_info]
    all_decisions = influx.query_decision_history_multi(ids, hours)

    # Per-ruleset counts
    per_ruleset = []
    agg: dict[str, int] = {"green": 0, "orange": 0, "red": 0}
    hourly_buckets: dict[int, dict] = {h: {"green": 0, "orange": 0, "red": 0} for h in range(24)}
    daily_buckets: dict[str, dict] = {}

    for rs_info in rulesets_info:
        rid = rs_info["id"]
        rows = all_decisions.get(rid, [])
        counts: dict[str, int] = {"green": 0, "orange": 0, "red": 0}
        for row in rows:
            d = row.get("decision", "")
            if d in counts:
                counts[d] += 1
                agg[d] += 1
                ts = _to_dt(row["timestamp"])
                hourly_buckets[ts.astimezone(timezone.utc).hour][d] += 1
                date_str = ts.astimezone(timezone.utc).strftime("%Y-%m-%d")
                if date_str not in daily_buckets:
                    daily_buckets[date_str] = {"date": date_str, "green": 0, "orange": 0, "red": 0}
                daily_buckets[date_str][d] += 1
        total = sum(counts.values())
        per_ruleset.append({
            "id": rid,
            "name": rs_info["name"],
            "site_type": rs_info["site_type"],
            "green_pct": round(counts["green"] / total * 100, 1) if total else 0.0,
            "orange_pct": round(counts["orange"] / total * 100, 1) if total else 0.0,
            "red_pct": round(counts["red"] / total * 100, 1) if total else 0.0,
            "total": total,
        })

    per_ruleset.sort(key=lambda x: x["green_pct"], reverse=True)

    total_agg = sum(agg.values())
    aggregate = {
        "green_pct": round(agg["green"] / total_agg * 100, 1) if total_agg else 0.0,
        "orange_pct": round(agg["orange"] / total_agg * 100, 1) if total_agg else 0.0,
        "red_pct": round(agg["red"] / total_agg * 100, 1) if total_agg else 0.0,
        "total": total_agg,
        "green_count": agg["green"],
        "orange_count": agg["orange"],
        "red_count": agg["red"],
    }

    hourly = []
    for h in range(24):
        b = hourly_buckets[h]
        total_h = sum(b.values())
        hourly.append({
            "hour": h,
            "green_pct": round(b["green"] / total_h * 100, 1) if total_h else 0.0,
            "orange_pct": round(b["orange"] / total_h * 100, 1) if total_h else 0.0,
            "red_pct": round(b["red"] / total_h * 100, 1) if total_h else 0.0,
            "total": total_h,
        })

    flyable = sorted(daily_buckets.values(), key=lambda x: x["date"])

    return {
        "period_hours": hours,
        "per_ruleset": per_ruleset,
        "aggregate": aggregate,
        "hourly_pattern": hourly,
        "flyable_days": flyable,
    }


def _to_dt(ts) -> datetime:
    if isinstance(ts, str):
        return datetime.fromisoformat(ts)
    return ts


# ---------------------------------------------------------------------------
# Flyable days — daily green/orange/red counts
# ---------------------------------------------------------------------------

def flyable_days(influx: InfluxClient, ruleset_id: str, hours: int = 720) -> list[dict]:
    """Return per-calendar-day decision counts: [{date, green, orange, red}]."""
    rows = _fetch_decisions(influx, ruleset_id, hours)
    daily: dict[str, dict] = {}
    for row in rows:
        date_str = _to_dt(row["timestamp"]).astimezone(timezone.utc).strftime("%Y-%m-%d")
        if date_str not in daily:
            daily[date_str] = {"date": date_str, "green": 0, "orange": 0, "red": 0}
        d = row.get("decision", "")
        if d in daily[date_str]:
            daily[date_str][d] += 1
    return sorted(daily.values(), key=lambda x: x["date"])


# ---------------------------------------------------------------------------
# Hourly pattern — % green/orange/red per hour of day (UTC)
# ---------------------------------------------------------------------------

def hourly_pattern(influx: InfluxClient, ruleset_id: str, hours: int = 720) -> list[dict]:
    """Return 24 entries [{hour, green_pct, orange_pct, red_pct, total}]."""
    rows = _fetch_decisions(influx, ruleset_id, hours)
    buckets: dict[int, dict] = {h: {"green": 0, "orange": 0, "red": 0} for h in range(24)}
    for row in rows:
        hour = _to_dt(row["timestamp"]).astimezone(timezone.utc).hour
        d = row.get("decision", "")
        if d in buckets[hour]:
            buckets[hour][d] += 1
    result = []
    for h in range(24):
        total = sum(buckets[h].values())
        if total == 0:
            result.append({"hour": h, "green_pct": 0.0, "orange_pct": 0.0, "red_pct": 0.0, "total": 0})
        else:
            result.append({
                "hour": h,
                "green_pct": round(buckets[h]["green"] / total * 100, 1),
                "orange_pct": round(buckets[h]["orange"] / total * 100, 1),
                "red_pct": round(buckets[h]["red"] / total * 100, 1),
                "total": total,
            })
    return result


# ---------------------------------------------------------------------------
# Monthly breakdown — % green/orange/red per calendar month
# ---------------------------------------------------------------------------

def monthly_breakdown(influx: InfluxClient, ruleset_id: str, hours: int = 8760) -> list[dict]:
    """Return [{month, green_pct, orange_pct, red_pct, total}] sorted chronologically."""
    rows = _fetch_decisions(influx, ruleset_id, hours)
    monthly: dict[str, dict] = {}
    for row in rows:
        month_str = _to_dt(row["timestamp"]).astimezone(timezone.utc).strftime("%Y-%m")
        if month_str not in monthly:
            monthly[month_str] = {"month": month_str, "green": 0, "orange": 0, "red": 0}
        d = row.get("decision", "")
        if d in monthly[month_str]:
            monthly[month_str][d] += 1
    result = []
    for month_str, counts in sorted(monthly.items()):
        total = counts["green"] + counts["orange"] + counts["red"]
        result.append({
            "month": month_str,
            "green_pct": round(counts["green"] / total * 100, 1) if total else 0.0,
            "orange_pct": round(counts["orange"] / total * 100, 1) if total else 0.0,
            "red_pct": round(counts["red"] / total * 100, 1) if total else 0.0,
            "total": total,
        })
    return result


# ---------------------------------------------------------------------------
# Condition trigger rate — per-condition colour distribution
# ---------------------------------------------------------------------------

def condition_trigger_rate(influx: InfluxClient, ruleset_id: str, hours: int = 720) -> list[dict]:
    """
    Parse condition_results JSON from every decision row and aggregate per condition.

    Returns [{condition_id, field, station_id, operator, value_a, value_b,
              green_pct, orange_pct, red_pct, total}] sorted by red_pct desc.
    """
    rows = _fetch_decisions(influx, ruleset_id, hours)
    cond_counts: dict[str, dict] = {}
    cond_meta: dict[str, dict] = {}

    for row in rows:
        json_str = row.get("condition_results_json")
        if not json_str:
            continue
        try:
            conditions = json.loads(json_str)
        except (json.JSONDecodeError, TypeError):
            continue
        for cond in conditions:
            cid = cond.get("condition_id", "")
            if not cid:
                continue
            if cid not in cond_counts:
                cond_counts[cid] = {"green": 0, "orange": 0, "red": 0}
                cond_meta[cid] = {
                    "condition_id": cid,
                    "field": cond.get("field", ""),
                    "station_id": cond.get("station_id", ""),
                    "operator": cond.get("operator", ""),
                    "value_a": cond.get("value_a"),
                    "value_b": cond.get("value_b"),
                }
            colour = cond.get("result_colour", "")
            if colour in cond_counts[cid]:
                cond_counts[cid][colour] += 1

    result = []
    for cid, counts in cond_counts.items():
        total = counts["green"] + counts["orange"] + counts["red"]
        result.append({
            **cond_meta[cid],
            "green_pct": round(counts["green"] / total * 100, 1) if total else 0.0,
            "orange_pct": round(counts["orange"] / total * 100, 1) if total else 0.0,
            "red_pct": round(counts["red"] / total * 100, 1) if total else 0.0,
            "total": total,
        })
    result.sort(key=lambda x: x["red_pct"], reverse=True)
    return result


# ---------------------------------------------------------------------------
# Best windows — longest consecutive green streaks
# ---------------------------------------------------------------------------

def best_windows(influx: InfluxClient, ruleset_id: str, hours: int = 720) -> list[dict]:
    """
    Return the top 5 longest consecutive green streaks.

    Each entry: {start, end, duration_hours, count}.
    """
    rows = sorted(_fetch_decisions(influx, ruleset_id, hours), key=lambda r: r["timestamp"])

    streaks: list[list] = []
    current: list = []
    for row in rows:
        if row.get("decision") == "green":
            current.append(row)
        else:
            if current:
                streaks.append(current)
            current = []
    if current:
        streaks.append(current)

    result = []
    for streak in streaks:
        start_ts = _to_dt(streak[0]["timestamp"])
        end_ts = _to_dt(streak[-1]["timestamp"])
        duration = (end_ts - start_ts).total_seconds() / 3600.0
        result.append({
            "start": start_ts.isoformat(),
            "end": end_ts.isoformat(),
            "duration_hours": round(duration, 1),
            "count": len(streak),
        })
    result.sort(key=lambda x: x["duration_hours"], reverse=True)
    return result[:5]


# ---------------------------------------------------------------------------
# Site comparison — green % across multiple rulesets
# ---------------------------------------------------------------------------

def site_comparison(influx: InfluxClient, ruleset_ids: list[str], hours: int = 720) -> list[dict]:
    """Return [{ruleset_id, green_pct, orange_pct, red_pct, total}] for each ruleset."""
    result = []
    for rid in ruleset_ids:
        rows = _fetch_decisions(influx, rid, hours)
        counts: dict[str, int] = {"green": 0, "orange": 0, "red": 0}
        for row in rows:
            d = row.get("decision", "")
            if d in counts:
                counts[d] += 1
        total = sum(counts.values())
        result.append({
            "ruleset_id": rid,
            "green_pct": round(counts["green"] / total * 100, 1) if total else 0.0,
            "orange_pct": round(counts["orange"] / total * 100, 1) if total else 0.0,
            "red_pct": round(counts["red"] / total * 100, 1) if total else 0.0,
            "total": total,
        })
    return result
