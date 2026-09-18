"""Canonical naming for MCP tool calls across harness adapters.

Each harness (Codex, Claude Code, Gemini CLI) reports MCP tool calls in a
different format:

- Codex emits the server name and tool name as separate fields on the
  ``mcp_tool_call`` payload.
- Claude Code emits ``mcp__<server>__<tool>`` (double-underscore separators).
  Server names may contain single underscores.
- Gemini CLI emits ``mcp_<server>_<tool>`` (single-underscore separators).
  Upstream forbids underscores in the server name -- see
  ``packages/core/src/tools/mcp-tool.ts`` in google-gemini/gemini-cli, where
  the parser uses ``^([^_]+)_(.+)$`` -- so the format is unambiguous.

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

# Matches ``mcp_<server>_<tool>`` where the server segment contains no
# underscores. Mirrors the contract enforced by gemini-cli upstream so we
# parse the same way it formats.
_GEMINI_MCP_PATTERN = re.compile(r"^mcp_([^_]+)_(.+)$")


def canonical_tool_name(server: Optional[str], tool: str) -> str:
    """Return the canonical name for a tool call.

    Args:
        server: MCP server name, or an empty string / None for native tools.
        tool: Bare tool name as exposed by the server (or the native tool).

    Returns:
        ``<server>__<tool>`` when ``server`` is non-empty, otherwise ``tool``.
    """
    if not tool:
        return tool
    if server:
        return f"{server}{CANONICAL_SEPARATOR}{tool}"
    return tool


def parse_claude_mcp_tool_name(name: str) -> Optional[Tuple[str, str]]:
    """Parse a Claude Code MCP tool name into ``(server, tool)``.

    Claude Code's SDK reports MCP tools as ``mcp__<server>__<tool>``. Both
    the server and tool segments may contain single underscores; only the
    double-underscore acts as a separator. The first ``__`` after the
    ``mcp__`` prefix is treated as the server/tool boundary so the tool
    segment may itself contain ``__``.

    Returns:
        ``(server, tool)`` if ``name`` matches the expected format,
        otherwise ``None``.
    """
    if not name.startswith(_CLAUDE_MCP_PREFIX):
        return None
    remainder = name[len(_CLAUDE_MCP_PREFIX):]
    server, sep, tool = remainder.partition(CANONICAL_SEPARATOR)
    if not sep or not server or not tool:
        return None
    return server, tool


def parse_gemini_mcp_tool_name(name: str) -> Optional[Tuple[str, str]]:
    """Parse a Gemini CLI MCP tool name into ``(server, tool)``.

    Gemini CLI reports MCP tools as ``mcp_<server>_<tool>`` using a single
    underscore separator. The upstream parser requires the server segment
    to contain no underscores, which makes the split unambiguous even when
    the tool name itself contains underscores.

    Returns:
        ``(server, tool)`` if ``name`` matches the expected format,
        otherwise ``None``.
    """
    match = _GEMINI_MCP_PATTERN.match(name)
    if not match:
        return None
    return match.group(1), match.group(2)


def canonicalize_claude_tool_name(name: str) -> str:
    """Convert a Claude Code tool name to canonical form.

    MCP tools are reformatted; native tools (e.g. ``Read``, ``Bash``) pass
    through unchanged. If a name starts with ``mcp__`` but does not match
    the expected structure, it is returned as-is so the caller can still see
    and debug the raw value.
    """
    parsed = parse_claude_mcp_tool_name(name)
    if parsed is None:
        return name
    server, tool = parsed
    return canonical_tool_name(server, tool)


def canonicalize_gemini_tool_name(name: str) -> str:
    """Convert a Gemini CLI tool name to canonical form.

    MCP tools are reformatted; native tools pass through unchanged. If a
    name starts with ``mcp_`` but does not match the expected structure, it
    is returned as-is.
    """
    parsed = parse_gemini_mcp_tool_name(name)
    if parsed is None:
        return name
    server, tool = parsed
    return canonical_tool_name(server, tool)


# Antigravity (agy) does NOT expose MCP tools as ``mcp_<server>_<tool>``
# top-level functions. Confirmed from the v1.0.5 binary: it has a single
# native tool ``call_mcp_tool`` whose jsonschema is
# ``{ServerName, ToolName, Arguments}`` -- the real server/tool identity
# lives in the call *arguments*, not the tool name. So canonicalization
# must unwrap those args rather than pattern-match the name.
_AGY_MCP_WRAPPER = "call_mcp_tool"

# The v1.0.5 schema is ``{ServerName, ToolName, Arguments}``. The Go struct
# (confirmed in the agy binary) carries no ``json:`` tags -- only
# ``jsonschema:"required"`` / ``jsonschema_description`` -- so the JSON property
# names are exactly the Go field names. There are no casing variants to handle.
_AGY_SERVER_KEY = "ServerName"
_AGY_TOOL_KEY = "ToolName"


def _agy_decode_scalar(value) -> str:
    """Decode an agy tool-call arg value to a plain string.

    agy stores each ``call_mcp_tool`` arg value as a raw JSON token, so a
    string value arrives JSON-encoded *with* its surrounding quotes, e.g.
    ``args["ServerName"]`` is the 11-char string ``"cloud-sql"`` (quotes
    included). Round-tripping through ``json.loads`` strips the quoting;
    if the value isn't valid JSON we fall back to the raw string.
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
    """Parse an agy MCP wrapper call into ``(server, tool)``.

    Returns ``(server, tool)`` if ``name`` is the ``call_mcp_tool`` wrapper
    and both server and tool names are present in ``args``; otherwise
    ``None``. Server/tool values are JSON-decoded (agy quotes them).
    """
    if name != _AGY_MCP_WRAPPER or not isinstance(args, dict):
        return None
    server = args.get(_AGY_SERVER_KEY)
    tool = args.get(_AGY_TOOL_KEY)
    if not server or not tool:
        return None
    return _agy_decode_scalar(server), _agy_decode_scalar(tool)


def canonicalize_agy_tool_name(name: str, args: Optional[dict] = None) -> str:
    """Convert an agy tool name to canonical form.

    MCP calls arrive as the ``call_mcp_tool`` wrapper with the real
    server/tool in ``args``; those are unwrapped to ``<server>__<tool>``.
    Native agy tools (``run_command``, ``view_file``, ``write_to_file``,
    ``grep_search``, ...) pass through unchanged. A ``call_mcp_tool``
    whose args lack a usable server/tool pair is returned as-is so the
    raw value stays visible for debugging.
    """
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
