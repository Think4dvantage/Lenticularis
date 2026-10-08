"""Structural guarantees of the public MCP server (specs/010-mcp-server, invariants 3-4)."""
from __future__ import annotations

import ast
from pathlib import Path

import lenticularis.mcp_server as pkg

PKG_DIR = Path(pkg.__file__).parent

# Pilot-owned / write-capable modules the public package must never touch.
FORBIDDEN_IMPORTS = (
    "lenticularis.database.models",   # SQLAlchemy ORM: rulesets, users, conditions
    "lenticularis.database.db",
    "lenticularis.rules",
    "lenticularis.services.auth",
    "lenticularis.api.dependencies",
    "lenticularis.api.routers",
)
FORBIDDEN_ATTRS = ("write_", "delete_", "_write_api", "commit")


def _imports(path: Path) -> set[str]:
    out: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            out |= {a.name for a in node.names}
        elif isinstance(node, ast.ImportFrom) and node.module:
            out.add(node.module)
    return out


def test_no_pilot_or_auth_imports():
    offenders = {
        f.name: sorted(i for i in _imports(f) if i.startswith(FORBIDDEN_IMPORTS))
        for f in PKG_DIR.glob("*.py")
    }
    assert {k: v for k, v in offenders.items() if v} == {}


def test_package_never_calls_write_apis():
    bad: list[str] = []
    for f in PKG_DIR.glob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            if isinstance(node, ast.Attribute) and node.attr.startswith(FORBIDDEN_ATTRS):
                bad.append(f"{f.name}:{node.lineno} .{node.attr}")
    assert bad == []


def test_package_name_does_not_shadow_the_sdk():
    assert PKG_DIR.name == "mcp_server"


def test_default_allowlist_excludes_private_networks():
    from lenticularis.config import McpConfig

    nets = set(McpConfig().verified_networks)
    assert not nets & {"wunderground", "ecowitt", "foehn"}
    assert {"meteoswiss", "slf", "metar", "holfuy", "windline", "fga", "jfb"} == nets
