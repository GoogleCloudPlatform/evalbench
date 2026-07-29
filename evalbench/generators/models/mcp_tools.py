"""Generator that fetches an MCP endpoint's tools and renders man-page markup.

This plays the EvalBench *generator* role for the MCP readability check: instead
of asking a model to produce an artifact, it fetches the tool listing from the
"system under test" (an MCP endpoint) and renders the man-page markup that the
readability *scorer* then evaluates against the style guide.

The source is pluggable per endpoint via ``tools_source.type``. The type names
the *transport*, not the protocol -- ``http`` and ``stdio`` both speak MCP:
  - ``http``: live MCP ``tools/list`` over Streamable HTTP. The fetch URL is
    ``tools_source.url``; ``headers`` and ``authProviderType`` are honoured.
  - ``stdio``: launch a local MCP server via a command and speak MCP over its
    stdio pipes (official ``mcp`` SDK). The process is started before fetching,
    ``tools/list`` is called, then it is shut down. For local / command-launched
    servers such as MCP Toolbox.
  - ``file``: read a local spec in the raw ``tools/list`` format
    (``{"tools": [...]}``); dependency-free, for offline / deterministic runs.
"""

import json
import os

from mcp import types as mcp_types

from .generator import QueryGenerator
from .mcp_client import (
    McpToolsError,
    auth_headers,
    fetch_tools_http,
    fetch_tools_stdio,
)
from .mcp_tool_formatter import format_tools_to_man_page


class McpToolsGenerator(QueryGenerator):
    """Fetches an MCP endpoint's tools and renders them as man-page markup."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "mcp_tools"
        cfg = querygenerator_config or {}
        self.timeout = cfg.get("timeout", 30)

    # The abstract base requires generate_internal; the orchestrator calls
    # fetch_tools directly, but we keep this so the class is a valid generator.
    def generate_internal(self, prompt):
        if isinstance(prompt, dict):
            _, man_page = self.fetch_tools(prompt)
            return man_page
        return ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def fetch_tools(self, endpoint: dict) -> tuple[list[mcp_types.Tool], str]:
        """Fetch tools for ``endpoint`` and render man-page markup.

        Returns ``(tools, man_page)`` where ``tools`` is a list of
        ``mcp.types.Tool`` (used by the metrics scorer) and ``man_page`` is the
        rendered markup string (fed to the readability scorer).
        """
        source = self._resolve_source(endpoint)
        source_type = (source.get("type") or "http").lower()

        if source_type == "file":
            tools = self._from_file(source)
        elif source_type == "http":
            tools = self._from_http(source)
        elif source_type == "stdio":
            tools = self._from_stdio(source)
        else:
            raise McpToolsError(f"Unknown tools_source.type '{source_type}'")

        return tools, format_tools_to_man_page(tools)

    # ------------------------------------------------------------------
    # Source resolution
    # ------------------------------------------------------------------
    @staticmethod
    def _resolve_source(endpoint: dict) -> dict:
        """The endpoint's ``tools_source`` (or ``{}`` to default to ``http``)."""
        return dict(endpoint.get("tools_source") or {})

    # ------------------------------------------------------------------
    # http source (official SDK over Streamable HTTP)
    # ------------------------------------------------------------------
    def _from_http(self, source: dict) -> list[mcp_types.Tool]:
        raw_url = source.get("url")
        if not raw_url:
            raise McpToolsError("tools_source.type 'http' requires a 'url'")
        return fetch_tools_http(raw_url, auth_headers(source), self.timeout)

    # ------------------------------------------------------------------
    # stdio source (official SDK over a launched local process)
    # ------------------------------------------------------------------
    def _from_stdio(self, source: dict) -> list[mcp_types.Tool]:
        command = source.get("command")
        if not command:
            raise McpToolsError("tools_source.type 'stdio' requires a 'command'")
        # Merge the current environment with any per-source overrides so the
        # launched server inherits PATH etc. but can be given extra config.
        env = source.get("env")
        if env is not None:
            merged_env = dict(os.environ)
            merged_env.update({str(k): str(v) for k, v in env.items()})
            env = merged_env
        return fetch_tools_stdio(
            command, source.get("args"), env, source.get("cwd")
        )

    # ------------------------------------------------------------------
    # file source
    # ------------------------------------------------------------------
    def _from_file(self, source: dict) -> list[mcp_types.Tool]:
        path = source.get("path")
        if not path:
            raise McpToolsError("tools_source.type 'file' requires a 'path'")
        if not os.path.exists(path):
            raise McpToolsError(f"Tools file not found: {path}")
        try:
            with open(path, "r") as f:
                parsed = json.loads(f.read())  # raw tools/list JSON dump
        except Exception as e:
            raise McpToolsError(f"Could not parse tools file {path}: {e}") from e
        if not parsed:
            raise McpToolsError(f"Empty or unreadable tools file: {path}")
        return self._to_tools(parsed)

    @staticmethod
    def _to_tools(raw) -> list[mcp_types.Tool]:
        """Build ``mcp.types.Tool`` objects from a raw ``tools/list`` spec.

        The spec must match the raw ``tools/list`` output: an object with a
        top-level ``tools`` array, each entry a tool with ``name``,
        ``description``, and ``inputSchema`` (JSON Schema).
        """
        if not isinstance(raw, dict) or not isinstance(raw.get("tools"), list):
            raise McpToolsError(
                "tools file must be the raw tools/list output: a JSON object "
                "with a top-level 'tools' array"
            )
        tools = raw["tools"]
        if not tools:
            raise McpToolsError("tools/list spec contains no tools")

        built: list[mcp_types.Tool] = []
        for t in tools:
            if not isinstance(t, dict):
                raise McpToolsError(f"Invalid tool entry (not an object): {t!r}")
            schema = t.get("inputSchema") or {}
            if not isinstance(schema, dict):
                schema = {}
            try:
                built.append(
                    mcp_types.Tool(
                        name=t.get("name", ""),
                        description=t.get("description") or "",
                        inputSchema=schema,
                    )
                )
            except Exception as e:
                raise McpToolsError(f"Invalid tool entry {t!r}: {e}") from e
        return built
