"""
Public, read-only MCP server (specs/010-mcp-server).

Deliberately named ``mcp_server`` so it can never shadow the ``mcp`` SDK dependency.
Nothing in this package may import pilot-owned models (rule sets, users, decisions) —
``tests/backend/test_mcp_invariants.py`` enforces that.
"""
