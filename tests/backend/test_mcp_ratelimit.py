"""Rate limiter + caller identity for the public MCP endpoint."""
from __future__ import annotations

from lenticularis.mcp_server.ratelimit import RateLimiter, caller_key


def test_allows_up_to_limit_then_blocks_with_retry_after():
    rl = RateLimiter(per_minute=3, max_callers=10)
    assert [rl.check("a", now=100.0 + i)[0] for i in range(3)] == [True, True, True]
    allowed, retry = rl.check("a", now=103.0)
    assert allowed is False and 1 <= retry <= 60


def test_window_slides():
    rl = RateLimiter(per_minute=1, max_callers=10)
    assert rl.check("a", now=0.0)[0] is True
    assert rl.check("a", now=30.0)[0] is False
    assert rl.check("a", now=61.0)[0] is True


def test_callers_are_independent():
    rl = RateLimiter(per_minute=1, max_callers=10)
    assert rl.check("a", now=0.0)[0] is True
    assert rl.check("b", now=0.0)[0] is True


def test_caller_table_is_bounded():
    rl = RateLimiter(per_minute=5, max_callers=3)
    for i in range(50):
        rl.check(f"c{i}", now=float(i))
    assert len(rl) <= 3


def test_key_uses_rightmost_forwarded_entry_not_client_supplied_left_entries():
    spoofed = {"x-forwarded-for": "1.2.3.4, 9.9.9.9"}       # client claims 1.2.3.4; proxy saw 9.9.9.9
    honest = {"x-forwarded-for": "9.9.9.9"}
    assert caller_key(spoofed, "10.0.0.1", 1) == caller_key(honest, "10.0.0.1", 1)


def test_key_falls_back_to_peer_and_never_exposes_raw_ip():
    k = caller_key({}, "203.0.113.7", 1)
    assert "203.0.113.7" not in k and len(k) == 16
    assert k == caller_key({}, "203.0.113.7", 1)
    assert k != caller_key({}, "203.0.113.8", 1)
