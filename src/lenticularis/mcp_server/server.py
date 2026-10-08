"""
MCP transport glue for the public weather server (specs/010-mcp-server).

Stateless Streamable HTTP with JSON responses: no sessions, no SSE — compatible with the app's
GZip / BaseHTTPMiddleware stack. Everything that can go wrong on the way in (rate limit, unexpected
exceptions) is converted to a caller-friendly tool error here so the SDK's default error text can
never leak a Flux query or hostname.
"""
from __future__ import annotations

import contextlib
import logging
import time
from typing import Annotated, Any, AsyncIterator, Callable, Optional

from mcp.server.fastmcp import Context, FastMCP
from mcp.server.fastmcp.exceptions import ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import Field
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from lenticularis.config import McpConfig
from lenticularis.mcp_server.ratelimit import RateLimiter, caller_key, xff_entry_count
from lenticularis.mcp_server.tools import McpToolError, PublicWeatherTools
from lenticularis.mcp_server.usage import UsageStats

logger = logging.getLogger(__name__)

_READ_ONLY = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)

_INSTRUCTIONS = (
    "Weather for Swiss weather stations: current, past and forecast values plus föhn status. "
    "Station ids are not guessable — call search_stations first (by name or lat/lon). "
    "All times are UTC; every response carries a units map. Only verified stations are served."
)


class McpHandle:
    """Owns the FastMCP instance (one per app) and exposes the ASGI endpoint + lifecycle."""

    def __init__(self, cfg: McpConfig, state_getter: Callable[[], Any], version: str) -> None:
        self.cfg = cfg
        self.usage = UsageStats()
        self.limiter = RateLimiter(cfg.rate_limit_per_minute, cfg.rate_limit_max_callers)
        self.tools = PublicWeatherTools(state_getter, cfg, version)
        self._xff_logged = False
        self.fastmcp = self._build()
        self._asgi: ASGIApp = self.fastmcp.streamable_http_app()

    # -- construction --------------------------------------------------------

    def _build(self) -> FastMCP:
        hosts: list[str] = []
        for h in self.cfg.allowed_hosts:
            hosts += [h, f"{h}:*"]
        # FastMCP.__init__ reconfigures the ROOT logger (basicConfig). The app owns logging, so
        # snapshot and restore it — otherwise the app's log format/level silently change.
        root = logging.getLogger()
        saved_handlers, saved_level = list(root.handlers), root.level
        try:
            mcp = FastMCP(
                "Lenticularis",
                instructions=_INSTRUCTIONS,
                stateless_http=True,
                json_response=True,
                streamable_http_path="/",
                transport_security=TransportSecuritySettings(
                    enable_dns_rebinding_protection=True, allowed_hosts=hosts, allowed_origins=[],
                ),
            )
        finally:
            root.handlers[:] = saved_handlers
            root.setLevel(saved_level)
        self._register(mcp)
        return mcp

    def _register(self, mcp: FastMCP) -> None:
        t = self.tools
        guard = self._guard

        @mcp.tool(name="search_stations", annotations=_READ_ONLY, description=(
            "Find weather stations by (partial) name, or by location. CALL THIS FIRST — station ids "
            "are not guessable. Name matching ignores accents (Zürich = Zuerich). With lat+lon, results "
            "are nearest-first within radius_km (default 25)."))
        async def search_stations(
            ctx: Context,
            query: Annotated[Optional[str], Field(description="Part of the station name, e.g. 'Jungfrau'")] = None,
            lat: Annotated[Optional[float], Field(description="Latitude (WGS84); needs lon")] = None,
            lon: Annotated[Optional[float], Field(description="Longitude (WGS84); needs lat")] = None,
            radius_km: Annotated[Optional[float], Field(description="Search radius, max 200 (needs lat/lon)")] = None,
            network: Annotated[Optional[str], Field(description="Restrict to one network, e.g. meteoswiss")] = None,
            canton: Annotated[Optional[str], Field(description="Two-letter canton code, e.g. BE")] = None,
            limit: Annotated[int, Field(description="Max results (default 10)")] = 10,
        ) -> dict[str, Any]:
            return await guard("search_stations", ctx, lambda: t.search_stations(
                query, lat, lon, radius_km, network, canton, limit))

        @mcp.tool(name="get_current_weather", annotations=_READ_ONLY, description=(
            "Latest measurements (wind, gust, direction, temperature, humidity, pressure, precipitation) "
            "for one station, with the measurement time, its age and a stale flag. Fields a station does "
            "not measure are omitted."))
        async def get_current_weather(
            ctx: Context,
            station_id: Annotated[str, Field(description="Station id from search_stations")],
        ) -> dict[str, Any]:
            return await guard("get_current_weather", ctx, lambda: t.get_current_weather(station_id))

        @mcp.tool(name="get_weather_history", annotations=_READ_ONLY, description=(
            "Measured values for a past period of one station. Give EITHER hours (lookback, max 720) OR "
            "start+end (ISO-8601 UTC, max 365 days). Large ranges are aggregated: gusts=max, "
            "precipitation=sum, wind direction=last value, everything else=mean (stated in the response)."))
        async def get_weather_history(
            ctx: Context,
            station_id: Annotated[str, Field(description="Station id from search_stations")],
            hours: Annotated[Optional[int], Field(description="Lookback in hours, 1-720")] = None,
            start: Annotated[Optional[str], Field(description="Range start, ISO-8601 UTC")] = None,
            end: Annotated[Optional[str], Field(description="Range end, ISO-8601 UTC")] = None,
            fields: Annotated[Optional[list[str]], Field(description="Subset of fields; default all")] = None,
            resolution: Annotated[str, Field(description="auto | 10m | 30m | 1h | 3h | 6h | 12h | 1d")] = "auto",
        ) -> dict[str, Any]:
            return await guard("get_weather_history", ctx, lambda: t.get_weather_history(
                station_id, hours, start, end, fields, resolution))

        @mcp.tool(name="get_forecast", annotations=_READ_ONLY, description=(
            "Hourly forecast for one station (ICON-CH ensemble median plus *_min/*_max spread). Reports "
            "the model, when the run was issued, and any hours with no usable data (never interpolated)."))
        async def get_forecast(
            ctx: Context,
            station_id: Annotated[str, Field(description="Station id from search_stations")],
            hours: Annotated[int, Field(description="Horizon in hours, 1-120 (default 48)")] = 48,
        ) -> dict[str, Any]:
            return await guard("get_forecast", ctx, lambda: t.get_forecast(station_id, hours))

        @mcp.tool(name="get_foehn_status", annotations=_READ_ONLY, description=(
            "Föhn state (active / partial / inactive / no_data) for the Swiss föhn regions plus the "
            "north-south pressure gradients. Omit 'at' for live; a past time gives the observed state; "
            "a future time (up to 120 h) gives the forecast state."))
        async def get_foehn_status(
            ctx: Context,
            at: Annotated[Optional[str], Field(description="ISO-8601 UTC time; omit for now")] = None,
        ) -> dict[str, Any]:
            return await guard("get_foehn_status", ctx, lambda: t.get_foehn_status(at))

        @mcp.tool(name="describe_service", annotations=_READ_ONLY, description=(
            "Describe what this service covers: networks and station counts, units, data policy, update "
            "cadence and known limitations. Use it to answer questions about trustworthiness."))
        async def describe_service(ctx: Context) -> dict[str, Any]:
            return await guard("describe_service", ctx, lambda: t.describe_service())

        @mcp.resource("lenticularis://about", name="about", mime_type="application/json",
                      description="Coverage, units, data policy and limitations of this service.")
        async def about() -> dict[str, Any]:
            return await t.describe_service()

    # -- guard: rate limit, usage, error mapping ------------------------------

    async def _guard(self, name: str, ctx: Context, call: Callable[[], Any]) -> dict[str, Any]:
        headers: dict[str, str] = {}
        host: Optional[str] = None
        try:
            request = ctx.request_context.request
            if request is not None:
                headers = {k.lower(): v for k, v in request.headers.items()}
                host = request.client.host if request.client else None
        except Exception:  # no HTTP request context (e.g. direct call in tests)
            pass
        caller = caller_key(headers, host, self.cfg.trusted_proxy_hops)
        if not self._xff_logged:
            self._xff_logged = True
            logger.info(
                "[Lenti:mcp] first call: X-Forwarded-For entries=%d trusted_proxy_hops=%d "
                "(if a CDN sits in front, raise trusted_proxy_hops)",
                xff_entry_count(headers), self.cfg.trusted_proxy_hops,
            )

        allowed, retry = self.limiter.check(caller)
        if not allowed:
            self.usage.record_rate_limited()
            logger.info("[Lenti:mcp] tool=%s caller=%s rate_limited retry_after=%ds", name, caller, retry)
            raise ToolError(f"RATE_LIMITED: too many requests. Retry in {retry} seconds.")

        t0 = time.monotonic()
        ok = True
        try:
            return await call()
        except McpToolError as exc:
            ok = False
            raise ToolError(f"{exc.code}: {exc.message}") from None
        except ToolError:
            ok = False
            raise
        except Exception:
            ok = False
            logger.exception("[Lenti:mcp] tool=%s failed unexpectedly", name)
            raise ToolError("INTERNAL_ERROR: the weather service failed to answer. Try again later.") from None
        finally:
            ms = (time.monotonic() - t0) * 1000
            self.usage.record(name, caller, ok, ms)
            logger.info("[Lenti:mcp] tool=%s caller=%s ok=%s ms=%.0f", name, caller, ok, ms)

    # -- ASGI + lifecycle -----------------------------------------------------

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        """Forward ``/mcp`` and ``/mcp/`` to the stateless app at its root path (no 307 redirect)."""
        inner = dict(scope)
        inner["path"] = "/"
        inner["raw_path"] = b"/"
        await self._asgi(inner, receive, send)

    @contextlib.asynccontextmanager
    async def run(self) -> AsyncIterator[None]:
        """Enter the session manager's task group; call once from the app lifespan."""
        async with self.fastmcp.session_manager.run():
            logger.info(
                "[Lenti:mcp] enabled path=/mcp networks=%s allowed_hosts=%s rate_limit=%d/min "
                "trusted_proxy_hops=%d max_history_points=%d",
                self.cfg.verified_networks, self.cfg.allowed_hosts, self.cfg.rate_limit_per_minute,
                self.cfg.trusted_proxy_hops, self.cfg.max_history_points,
            )
            yield


class McpEndpoint:
    """Route endpoint registered in ``create_app()``; resolves the handle lazily per request.

    The handle is built in the lifespan (config is available there), so at import time the app
    only knows the route. Until the lifespan has run (or when disabled) callers get a clean 503.
    """

    def __init__(self, state_getter: Callable[[], Any]) -> None:
        self._state = state_getter

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        handle: Optional[McpHandle] = getattr(self._state(), "mcp", None)
        if handle is None:
            resp = JSONResponse(
                {"error": {"code": "UNAVAILABLE", "message": "MCP server is not enabled.", "details": {}}},
                status_code=503,
            )
            await resp(scope, receive, send)
            return
        await handle(scope, receive, send)
