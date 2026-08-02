"""
Regression test: query_forecast_snapshot_for_stations must use the slow (60s)
InfluxDB query client, not the default 10s one.

Context: this query is now called from two places under real-world concurrent
load (GET /api/foehn/forecast and rules/evaluator.py's run_forecast_evaluation_at,
specs/007-replay-aware-ruleset-decisions) and was observed timing out on the
default 10s client in production, causing spurious no-data fail-safe results.
query_forecast_replay already uses the slow client for the same reason.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from unittest.mock import MagicMock

from lenticularis.config import InfluxDBConfig
from lenticularis.database.influx import InfluxClient


def _make_client() -> InfluxClient:
    cfg = InfluxDBConfig(url="http://localhost:8086", token="test", org="test", bucket="test")
    client = InfluxClient(cfg)
    client._query_api = MagicMock(query=MagicMock(return_value=[]))
    client._slow_query_api = MagicMock(query=MagicMock(return_value=[]))
    return client


def test_forecast_snapshot_uses_slow_query_client():
    client = _make_client()
    client.query_forecast_snapshot_for_stations(["s1"], datetime.now(timezone.utc))
    client._slow_query_api.query.assert_called_once()
    client._query_api.query.assert_not_called()


# ---------------------------------------------------------------------------
# Regression: contains(value:, set:) is 135x slower than an OR-chain of == against
# weather_forecast / weather_forecast_thermal, whose per-hour init_date tag
# fragments them into a huge number of series that contains() cannot use the tag
# index to skip. Measured live: 9.3s (contains()) vs 69ms (OR-chain). Fixed 2026-08-03.
# ---------------------------------------------------------------------------

def _flux_arg(mock_query) -> str:
    args, kwargs = mock_query.call_args
    return args[0] if args else kwargs["query"]


def test_forecast_snapshot_uses_or_chain_not_contains():
    client = _make_client()
    client.query_forecast_snapshot_for_stations(["s1", "s2"], datetime.now(timezone.utc))
    flux = _flux_arg(client._slow_query_api.query)
    assert "contains(" not in flux
    assert 'r.station_id == "s1"' in flux
    assert 'r.station_id == "s2"' in flux


def test_thermal_forecast_snapshot_uses_or_chain_not_contains():
    client = _make_client()
    client.query_thermal_forecast_snapshot_for_stations(["s1", "s2"], datetime.now(timezone.utc))
    flux = _flux_arg(client._slow_query_api.query)
    assert "contains(" not in flux
    assert 'r.station_id == "s1"' in flux
    assert 'r.station_id == "s2"' in flux


def test_foehn_pressure_history_forecast_leg_uses_or_chain_not_contains():
    client = _make_client()
    # center_time in the future forces the forecast leg (weather_forecast) to run.
    future = datetime.now(timezone.utc) + timedelta(hours=6)
    client.query_foehn_pressure_history(["s1", "s2"], hours=4, center_time=future)
    # Both the observed and forecast legs run through _query_api (aggregateWindow
    # queries aren't snapshot lookups, so they stay on the fast client) — inspect
    # every call made, since either leg could run first.
    flux_calls = [c.args[0] if c.args else c.kwargs["query"] for c in client._query_api.query.call_args_list]
    assert flux_calls, "expected at least one query on the forecast leg"
    assert all("contains(" not in flux for flux in flux_calls)
    assert any('r.station_id == "s1"' in flux for flux in flux_calls)
