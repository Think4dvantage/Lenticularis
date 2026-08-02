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

from datetime import datetime, timezone
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
