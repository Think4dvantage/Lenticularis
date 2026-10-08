"""MCP transport: no redirect, host protection, tool calls, rate limit, error hygiene, logging."""
from __future__ import annotations

import asyncio
import json
import logging

import pytest_asyncio
from httpx import ASGITransport, AsyncClient

from lenticularis.config import McpConfig
from lenticularis.mcp_server.registry import McpRegistry
from lenticularis.mcp_server.server import McpHandle
from lenticularis.models.weather import WeatherStation

H = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _rpc(method: str, params: dict | None = None, id_: int = 1) -> dict:
    return {"jsonrpc": "2.0", "id": id_, "method": method, "params": params or {}}


def _init() -> dict:
    return _rpc("initialize", {"protocolVersion": "2025-06-18", "capabilities": {},
                               "clientInfo": {"name": "t", "version": "1"}})


@pytest_asyncio.fixture
async def mcp_app(test_app):
    cfg = McpConfig(allowed_hosts=["test", "lenti.cloud"], rate_limit_per_minute=1000)
    station = WeatherStation(station_id="meteoswiss-JUN", name="Jungfraujoch", network="meteoswiss",
                             latitude=46.5, longitude=8.0, elevation=3571, canton="BE")
    ecowitt = WeatherStation(station_id="ecowitt-1", name="Garden", network="ecowitt",
                             latitude=46.6, longitude=8.1)
    raw = {s.station_id: s for s in (station, ecowitt)}
    test_app.state.station_registry = raw
    reg = McpRegistry(cfg.verified_networks)
    reg.rebuild(raw, 300.0)
    test_app.state.mcp_registry = reg
    handle = McpHandle(cfg, lambda: test_app.state, "test")
    test_app.state.mcp = handle
    # ASGITransport never runs the lifespan, so enter the session manager by hand. anyio cancel
    # scopes must exit in the task that entered them, and a pytest-asyncio fixture's setup and
    # teardown run in different tasks — so own the scope in one dedicated task.
    started, stop = asyncio.Event(), asyncio.Event()

    async def _runner():
        async with handle.run():
            started.set()
            await stop.wait()

    task = asyncio.create_task(_runner())
    await started.wait()
    yield test_app
    stop.set()
    await task


@pytest_asyncio.fixture
async def mcp_client(mcp_app):
    async with AsyncClient(transport=ASGITransport(app=mcp_app), base_url="http://test") as c:
        yield c


async def _call(c, name, args, host="test"):
    r = await c.post("/mcp", headers={**H, "Host": host}, json=_rpc("tools/call", {"name": name, "arguments": args}))
    assert r.status_code == 200, r.text
    return r.json()["result"]


async def test_post_mcp_without_slash_is_200_not_a_redirect(mcp_client):
    r = await mcp_client.post("/mcp", headers=H, json=_init(), follow_redirects=False)
    assert r.status_code == 200
    assert r.json()["result"]["serverInfo"]["name"] == "Lenticularis"
    r2 = await mcp_client.post("/mcp/", headers=H, json=_init(), follow_redirects=False)
    assert r2.status_code == 200


async def test_unlisted_host_is_rejected(mcp_client):
    r = await mcp_client.post("/mcp", headers={**H, "Host": "evil.example"}, json=_init())
    assert r.status_code == 421


async def test_real_hostname_accepted(mcp_client):
    r = await mcp_client.post("/mcp", headers={**H, "Host": "lenti.cloud"}, json=_init())
    assert r.status_code == 200


async def test_tools_list_has_the_six_read_only_tools(mcp_client):
    r = await mcp_client.post("/mcp", headers=H, json=_rpc("tools/list"))
    tools = {t["name"]: t for t in r.json()["result"]["tools"]}
    assert set(tools) == {"search_stations", "get_current_weather", "get_weather_history",
                          "get_forecast", "get_foehn_status", "describe_service"}
    assert all(t["annotations"]["readOnlyHint"] is True for t in tools.values())


async def test_search_tool_end_to_end_hides_private_station(mcp_client):
    res = await _call(mcp_client, "search_stations", {"query": "a"})
    payload = res.get("structuredContent") or json.loads(res["content"][0]["text"])
    ids = [s["station_id"] for s in payload["stations"]]
    assert "meteoswiss-JUN" in ids and "ecowitt-1" not in ids


async def test_tool_error_is_caller_friendly(mcp_client):
    res = await _call(mcp_client, "get_current_weather", {"station_id": "ecowitt-1"})
    assert res["isError"] is True
    assert "ENTITY_NOT_FOUND" in res["content"][0]["text"] and "search_stations" in res["content"][0]["text"]


async def test_unexpected_exception_does_not_leak_details(mcp_client, mcp_app, caplog):
    def boom(member_ids):
        raise RuntimeError('secret flux: from(bucket:"x") host=influx.internal')

    mcp_app.state.influx.query_latest_virtual = boom
    with caplog.at_level(logging.ERROR):
        res = await _call(mcp_client, "get_current_weather", {"station_id": "meteoswiss-JUN"})
    text = res["content"][0]["text"]
    assert res["isError"] is True and "INTERNAL_ERROR" in text
    assert "flux" not in text and "influx.internal" not in text
    assert any("failed unexpectedly" in r.message for r in caplog.records)   # logged server-side


async def test_rate_limit_is_an_llm_visible_tool_error(mcp_app, mcp_client):
    mcp_app.state.mcp.limiter = type(mcp_app.state.mcp.limiter)(per_minute=2, max_callers=10)
    for _ in range(2):
        await _call(mcp_client, "describe_service", {})
    res = await _call(mcp_client, "describe_service", {})
    assert res["isError"] is True and "RATE_LIMITED" in res["content"][0]["text"]
    assert "Retry in" in res["content"][0]["text"]
    assert mcp_app.state.mcp.usage.snapshot()["rate_limited"] == 1


async def test_constructing_the_server_does_not_change_root_logging():
    root = logging.getLogger()
    before = (list(root.handlers), root.level)
    McpHandle(McpConfig(), lambda: None, "t")
    assert (list(root.handlers), root.level) == before


async def test_endpoint_is_503_when_not_enabled(test_app):
    test_app.state.mcp = None
    async with AsyncClient(transport=ASGITransport(app=test_app), base_url="http://test") as c:
        r = await c.post("/mcp", headers=H, json=_init())
    assert r.status_code == 503 and r.json()["error"]["code"] == "UNAVAILABLE"


async def test_health_mcp_reports_usage(mcp_client):
    await _call(mcp_client, "describe_service", {})
    r = await mcp_client.get("/api/health/mcp")
    body = r.json()
    assert body["enabled"] is True and body["verified_stations"] == 1
    assert body["tools"]["describe_service"]["calls"] >= 1
