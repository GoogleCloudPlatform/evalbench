"""Canonical naming for MCP tool calls across harness adapters.

Each harness (Codex, Claude Code, Gemini CLI, Antigravity) reports MCP tool
calls in a different format:

- Codex emits the server name and tool name as separate fields on the
  ``mcp_tool_call`` payload.
- Claude Code emits ``mcp__<server>__<tool>`` (double-underscore separators).
  Server names may contain single underscores.
- Gemini CLI emits ``mcp_<server>_<tool>`` (single-underscore separators).
  Upstream forbids underscores in the server name -- see
  ``packages/core/src/tools/mcp-tool.ts`` in google-gemini/gemini-cli, where
  the parser uses ``^([^_]+)_(.+)$`` -- so the format is unambiguous.
- Antigravity (``agy``) emits a native ``call_mcp_tool`` call whose arguments
  follow the schema ``{ServerName, ToolName, Arguments}``.

This module converts each format into a single canonical string:

    <server>__<tool>     for MCP tools
    <tool>               for native/built-in tools (no server)

The double-underscore separator preserves server identity (so
``cloud-sql__list_instances`` and ``alloydb__list_instances`` stay distinct)
and matches the convention Claude Code already uses, while never colliding
with Gemini's single-underscore separator. Datasets store golden
``expected_trajectory`` entries in this same canonical form, allowing the
trajectory matcher to perform a plain string comparison without per-harness
special cases.
"""

from __future__ import annotations

import json
import re
from typing import Optional, Tuple


CANONICAL_SEPARATOR = "__"

_CLAUDE_MCP_PREFIX = "mcp__"
_GEMINI_MCP_PATTERN = re.compile(r"^mcp_([^_]+)_(.+)$")

_AGY_MCP_WRAPPER = "call_mcp_tool"
_AGY_SERVER_KEY = "ServerName"
_AGY_TOOL_KEY = "ToolName"


def canonical_tool_name(server: Optional[str], tool: str) -> str:
    """Return ``<server>__<tool>`` when ``server`` is non-empty, else ``tool``."""
    if not tool:
        return tool
    if server:
        return f"{server}{CANONICAL_SEPARATOR}{tool}"
    return tool


def parse_claude_mcp_tool_name(name: str) -> Optional[Tuple[str, str]]:
    """Parse ``mcp__<server>__<tool>`` into ``(server, tool)``, or ``None``.

    Splits on the first ``__`` after ``mcp__`` so ``tool`` may contain ``__``.
    """
    if not name.startswith(_CLAUDE_MCP_PREFIX):
        return None
    remainder = name[len(_CLAUDE_MCP_PREFIX):]
    server, sep, tool = remainder.partition(CANONICAL_SEPARATOR)
    if not sep or not server or not tool:
        return None
    return server, tool


def parse_gemini_mcp_tool_name(name: str) -> Optional[Tuple[str, str]]:
    """Parse ``mcp_<server>_<tool>`` into ``(server, tool)``, or ``None``."""
    match = _GEMINI_MCP_PATTERN.match(name)
    if not match:
        return None
    return match.group(1), match.group(2)


def canonicalize_claude_tool_name(name: str) -> str:
    """Return ``<server>__<tool>`` for Claude MCP calls, or ``name`` unchanged."""
    parsed = parse_claude_mcp_tool_name(name)
    if parsed is None:
        return name
    server, tool = parsed
    return canonical_tool_name(server, tool)


def canonicalize_gemini_tool_name(name: str) -> str:
    """Return ``<server>__<tool>`` for Gemini MCP calls, or ``name`` unchanged."""
    parsed = parse_gemini_mcp_tool_name(name)
    if parsed is None:
        return name
    server, tool = parsed
    return canonical_tool_name(server, tool)


def _agy_decode_scalar(value) -> str:
    """Decode an agy tool-call arg value to a plain string.

    agy stores each ``call_mcp_tool`` arg value as a raw JSON token, so a
    string value arrives JSON-encoded with surrounding quotes (e.g.
    ``"\"cloud-sql\""``). ``json.loads`` strips the quotes. Invalid JSON falls
    back to the raw string.
    """
    if isinstance(value, str):
        try:
            decoded = json.loads(value)
            if isinstance(decoded, str):
                return decoded
        except (json.JSONDecodeError, ValueError):
            pass
        return value
    return str(value)


def parse_agy_mcp_tool_call(name: str, args: Optional[dict]):
    """Return ``(server, tool)`` for a valid ``call_mcp_tool`` call, or ``None``."""
    if name != _AGY_MCP_WRAPPER or not isinstance(args, dict):
        return None
    server = args.get(_AGY_SERVER_KEY)
    tool = args.get(_AGY_TOOL_KEY)
    if not server or not tool:
        return None
    return _agy_decode_scalar(server), _agy_decode_scalar(tool)


def canonicalize_agy_tool_name(name: str, args: Optional[dict] = None) -> str:
    """Return ``<server>__<tool>`` for agy MCP calls, or ``name`` unchanged."""
    parsed = parse_agy_mcp_tool_call(name, args)
    if parsed is None:
        return name
    server, tool = parsed
    return canonical_tool_name(server, tool)


def looks_like_canonical_mcp_name(name: str) -> bool:
    """Return True iff ``name`` *looks like* canonical MCP form (``<server>__<tool>``).

    This is a structural check only -- any ``x__y`` with non-empty
    segments passes; there is no registry of real MCP servers to
    validate against. Native/built-in harness tools (Read, Bash,
    update_topic, run_shell_command, etc.) never contain the canonical
    separator, so the predicate is still good enough to distinguish MCP
    calls from harness-internal ones after canonicalization.
    """
    if not name:
        return False
    server, sep, tool = name.partition(CANONICAL_SEPARATOR)
    return bool(sep and server and tool)
