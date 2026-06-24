"""Generator that fetches an MCP endpoint's tools and renders man-page markup.

This plays the EvalBench *generator* role for the MCP readability check: instead
of asking a model to produce an artifact, it fetches the tool listing from the
"system under test" (an MCP endpoint) and renders the production-tested man-page
markup that the compliance *scorer* then evaluates against the style guide.

The source is pluggable per endpoint via ``tools_source.type``:
  - ``mcp``:  live MCP ``tools/list`` over Streamable HTTP using the official
    ``mcp`` Python SDK. Requires network access (and usually auth).
  - ``file``: read a local YAML/JSON tools spec (dependency-free; great for tests
    and for offline / deterministic runs).

Fetching, auth, and URL normalization mirror the tested Data Cloud MCP
readability tooling (``mcp_utils``): ADC bearer + ``x-goog-user-project`` headers
and a ``/mcp`` suffix convention.
"""

import functools
import logging
import os

import anyio
import httpx

from mcp import types as mcp_types
from mcp.client import session as mcp_session
from mcp.client.streamable_http import streamable_http_client

from .generator import QueryGenerator
from .mcp_tool_formatter import ManPageResult, format_tools_to_man_page
from util.config import load_yaml_config


class McpToolsError(Exception):
    """Raised when a tools spec cannot be fetched or parsed."""


class McpToolsGenerator(QueryGenerator):
    """Fetches an MCP endpoint's tools and renders them as man-page markup."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "mcp_tools"
        cfg = querygenerator_config or {}
        self.timeout = cfg.get("timeout", 30)
        self.default_headers = cfg.get("headers") or {}
        # Optional default auth mode applied when an endpoint doesn't specify one.
        self.default_auth = cfg.get("auth")

    # The abstract base requires generate_internal; the orchestrator calls
    # fetch_tools directly, but we keep this so the class is a valid generator.
    def generate_internal(self, prompt):
        if isinstance(prompt, dict):
            _, result = self.fetch_tools(prompt, {})
            return result.man_page
        return ""

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def fetch_tools(self, endpoint: dict, defaults: dict):
        """Fetch tools for ``endpoint`` and render man-page markup.

        Returns ``(tools, man_page_result)`` where ``tools`` is a list of
        ``mcp.types.Tool`` and ``man_page_result`` is a
        :class:`ManPageResult` (markup + token/tool metrics).
        """
        source = self._resolve_source(endpoint, defaults)
        source_type = (source.get("type") or "mcp").lower()

        if source_type == "file":
            tools = self._from_file(source)
        elif source_type == "mcp":
            tools = self._from_mcp(source, endpoint)
        else:
            raise McpToolsError(f"Unknown tools_source.type '{source_type}'")

        result = format_tools_to_man_page(tools)
        return tools, result

    # ------------------------------------------------------------------
    # Source resolution / auth
    # ------------------------------------------------------------------
    def _resolve_source(self, endpoint: dict, defaults: dict) -> dict:
        """Merge defaults.tools_source with the endpoint's tools_source."""
        merged = dict((defaults or {}).get("tools_source") or {})
        merged.update(endpoint.get("tools_source") or {})
        return merged

    def _auth_headers(self, source: dict) -> dict:
        """Build request headers: defaults + source + ADC bearer + project."""
        headers = dict(self.default_headers)
        headers.update(source.get("headers") or {})
        project = source.get("project")
        auth = source.get("auth", self.default_auth)
        if auth == "google_credentials":
            token, adc_project = self._google_credentials()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            if adc_project and "x-goog-user-project" not in headers:
                headers["x-goog-user-project"] = adc_project
        if project:
            headers["x-goog-user-project"] = project
        return headers

    @staticmethod
    def _google_credentials():
        """Best-effort ADC ``(token, project)``; returns ``(None, None)`` if N/A."""
        try:
            import google.auth
            from google.auth.transport.requests import Request

            creds, project = google.auth.default(
                scopes=["https://www.googleapis.com/auth/cloud-platform"]
            )
            creds.refresh(Request())
            return creds.token, project
        except Exception as e:  # pragma: no cover - depends on environment
            logging.warning("mcp_tools: could not obtain Google credentials: %s", e)
            return None, None

    @staticmethod
    def sanitize_url(url: str) -> str:
        """Ensure the URL has a scheme and ends with ``/mcp``.

        ``/mcp`` is the conventional path where ESF exposes the MCP protocol.
        """
        stripped_url = (url or "").strip()
        if stripped_url.startswith(("http://", "https://")):
            url_with_scheme = stripped_url
        elif "localhost" in stripped_url:
            url_with_scheme = f"http://{stripped_url}"
        else:
            url_with_scheme = f"https://{stripped_url}"

        rstripped_url = url_with_scheme.rstrip("/")
        if not rstripped_url.endswith("/mcp"):
            return f"{rstripped_url}/mcp"
        return rstripped_url

    # ------------------------------------------------------------------
    # mcp source (official SDK over Streamable HTTP)
    # ------------------------------------------------------------------
    def _from_mcp(self, source: dict, endpoint: dict) -> list[mcp_types.Tool]:
        raw_url = source.get("url") or endpoint.get("endpoint_url")
        if not raw_url:
            raise McpToolsError("tools_source.type 'mcp' requires an endpoint URL")
        url = self.sanitize_url(raw_url)
        headers = self._auth_headers(source)
        try:
            return anyio.run(
                functools.partial(self._async_fetch_tools, url, headers)
            )
        except McpToolsError:
            raise
        except Exception as e:
            raise McpToolsError(f"Failed to fetch tools from {url}: {e}") from e

    async def _async_fetch_tools(self, url: str, headers: dict) -> list[mcp_types.Tool]:
        """Connect to the MCP server over Streamable HTTP and list its tools."""
        timeout = httpx.Timeout(self.timeout, read=300.0)
        async with (
            httpx.AsyncClient(headers=headers, timeout=timeout) as http_client,
            streamable_http_client(url, http_client=http_client) as streams,
        ):
            reader, writer, _ = streams
            async with mcp_session.ClientSession(reader, writer) as session:
                await session.initialize()
                tools_response = await session.list_tools()
        return list(tools_response.tools)

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
            parsed = load_yaml_config(path)  # yaml.safe_load handles JSON too
        except Exception as e:
            raise McpToolsError(f"Could not parse tools file {path}: {e}") from e
        if not parsed:
            raise McpToolsError(f"Empty or unreadable tools file: {path}")
        return self._to_tools(parsed)

    @staticmethod
    def _to_tools(raw) -> list[mcp_types.Tool]:
        """Build ``mcp.types.Tool`` objects from a parsed tools spec."""
        if isinstance(raw, dict):
            tools = raw.get("tools")
            if tools is None and "result" in raw:
                tools = (raw.get("result") or {}).get("tools")
        elif isinstance(raw, list):
            tools = raw
        else:
            tools = None

        if not tools:
            raise McpToolsError("No tools found in spec")

        built: list[mcp_types.Tool] = []
        for t in tools:
            if not isinstance(t, dict):
                continue
            schema = (
                t.get("inputSchema")
                or t.get("input_schema")
                or t.get("parameters")
                or {}
            )
            if not isinstance(schema, dict):
                schema = {}
            try:
                built.append(
                    mcp_types.Tool(
                        name=t.get("name", ""),
                        description=t.get("description") or t.get("title") or "",
                        inputSchema=schema,
                    )
                )
            except Exception as e:
                raise McpToolsError(f"Invalid tool entry {t!r}: {e}") from e
        if not built:
            raise McpToolsError("Tools list contained no valid tool entries")
        return built
