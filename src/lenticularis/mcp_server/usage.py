"""Per-tool usage counters for the public MCP server (feeds /api/health and the log)."""
from __future__ import annotations

import threading
from typing import Any

_MAX_TRACKED_CALLERS = 5000


class UsageStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._tools: dict[str, dict[str, float]] = {}
        self._callers: set[str] = set()
        self.rate_limited = 0

    def record(self, tool: str, caller: str, ok: bool, ms: float) -> None:
        with self._lock:
            t = self._tools.setdefault(tool, {"calls": 0, "errors": 0, "total_ms": 0.0})
            t["calls"] += 1
            t["errors"] += 0 if ok else 1
            t["total_ms"] += ms
            if len(self._callers) < _MAX_TRACKED_CALLERS:
                self._callers.add(caller)

    def record_rate_limited(self) -> None:
        with self._lock:
            self.rate_limited += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            tools = {
                name: {
                    "calls": int(v["calls"]),
                    "errors": int(v["errors"]),
                    "avg_ms": round(v["total_ms"] / v["calls"], 1) if v["calls"] else 0.0,
                }
                for name, v in self._tools.items()
            }
            return {
                "tools": tools,
                "distinct_callers": len(self._callers),
                "rate_limited": self.rate_limited,
            }
