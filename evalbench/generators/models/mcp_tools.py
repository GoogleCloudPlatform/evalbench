"""Generator that fetches an MCP endpoint's tools and renders man-page markup.

This plays the EvalBench *generator* role for the MCP readability check: instead
of asking a model to produce an artifact, it fetches the tool listing from the
"system under test" (an MCP endpoint) and renders the man-page markup that the
readability *scorer* then evaluates against the style guide.

The source is pluggable per endpoint via ``tools_source.type``. The type names
the *transport*, not the protocol -- ``http`` and ``stdio`` both speak MCP:
  - ``http``: live MCP ``tools/list`` over Streamable HTTP using the official
    ``mcp`` Python SDK. The fetch URL is ``tools_source.url``. No authentication
    is configured -- the public endpoints are reached unauthenticated; auth can
    be added back later if a future endpoint requires it.
  - ``stdio``: launch a local MCP server via a command and speak MCP over its
    stdio pipes (official ``mcp`` SDK). The process is started before fetching,
    ``tools/list`` is called, then it is shut down. For local / command-launched
    servers such as MCP Toolbox.
  - ``file``: read a local spec in the raw ``tools/list`` format
    (``{"tools": [...]}``); dependency-free, for offline / deterministic runs.
"""

import functools
import json
import logging
import os

import anyio

from mcp import types as mcp_types
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client

from .generator import QueryGenerator
from .mcp_tool_formatter import format_tools_to_man_page
from .tool_naming import canonical_tool_name


class McpToolsError(Exception):
    """Raised when a tools spec cannot be fetched or parsed."""


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

    @staticmethod
    def sanitize_url(url: str) -> str:
        """Ensure the URL has a scheme and ends with ``/mcp``.

        ``/mcp`` is the conventional path where the MCP protocol is exposed.
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
    # http source (official SDK over Streamable HTTP, no auth)
    # ------------------------------------------------------------------
    def _from_http(self, source: dict) -> list[mcp_types.Tool]:
        raw_url = source.get("url")
        if not raw_url:
            raise McpToolsError("tools_source.type 'http' requires a 'url'")
        return self._fetch_http(raw_url, self._auth_headers(source))

    def _fetch_http(
        self, raw_url: str, headers: dict | None
    ) -> list[mcp_types.Tool]:
        url = self.sanitize_url(raw_url)
        try:
            return anyio.run(
                functools.partial(self._async_fetch_tools, url, headers)
            )
        except McpToolsError:
            raise
        except Exception as e:
            raise McpToolsError(f"Failed to fetch tools from {url}: {e}") from e

    async def _async_fetch_tools(
        self, url: str, headers: dict | None = None
    ) -> list[mcp_types.Tool]:
        """Connect to the MCP server over Streamable HTTP and list its tools."""
        async with streamablehttp_client(
            url, headers=headers, timeout=self.timeout
        ) as streams:
            reader, writer, _ = streams
            async with ClientSession(reader, writer) as session:
                await session.initialize()
                tools_response = await session.list_tools()
        return list(tools_response.tools)

    @staticmethod
    def _auth_headers(server_config: dict) -> dict | None:
        """Build request headers for an MCP server (configured headers + auth).

        ``authProviderType: google_credentials`` mints a bearer token from ADC
        for the server's OAuth scopes (default: cloud-platform). Returns ``None``
        when the server needs no headers.
        """
        headers = dict(server_config.get("headers") or {})
        if server_config.get("authProviderType") == "google_credentials":
            import google.auth
            import google.auth.transport.requests

            scopes = (server_config.get("oauth") or {}).get("scopes")
            if not scopes:
                raise McpToolsError(
                    "google_credentials auth requires oauth.scopes on the MCP "
                    "server config; none provided"
                )
            try:
                creds, _ = google.auth.default(scopes=scopes)
                creds.refresh(google.auth.transport.requests.Request())
            except Exception as e:
                raise McpToolsError(
                    "Failed to acquire GCP Application Default Credentials. "
                    "Run `gcloud auth application-default login`."
                ) from e
            headers["Authorization"] = f"Bearer {creds.token}"
        return headers or None

    def _fetch_server_tools(
        self, server_name: str, server_config: dict
    ) -> list[mcp_types.Tool]:
        """Raw tools/list for one MCP server (original names).

        Servers without an ``httpUrl`` are skipped (only Streamable HTTP servers
        are queried) and yield an empty list.
        """
        server_config = server_config or {}
        url = server_config.get("httpUrl") or server_config.get("url")
        if not url:
            logging.warning(
                "mcp_tools: server %r has no httpUrl; skipping", server_name
            )
            return []
        tools = self._fetch_http(url, self._auth_headers(server_config))
        logging.info("mcp_tools: %s -> %d tools", server_name, len(tools))
        return tools

    def fetch_tools_from_mcp_servers(
        self, mcp_servers: dict
    ) -> list[mcp_types.Tool]:
        """Aggregate tools across all configured MCP servers.

        Each tool is namespaced ``<server>__<tool>`` to match the
        ``expected_trajectory`` format in CUJ datasets. Raw per-server tools
        (original names) are available via :meth:`_fetch_server_tools`.
        """
        aggregated: list[mcp_types.Tool] = []
        for server_name, server_config in (mcp_servers or {}).items():
            aggregated.extend(
                mcp_types.Tool(
                    name=canonical_tool_name(server_name, t.name),
                    description=t.description or "",
                    inputSchema=t.inputSchema,
                )
                for t in self._fetch_server_tools(server_name, server_config)
            )
        return aggregated

    # ------------------------------------------------------------------
    # stdio source (official SDK over a launched local process)
    # ------------------------------------------------------------------
    def _from_stdio(self, source: dict) -> list[mcp_types.Tool]:
        command = source.get("command")
        if not command:
            raise McpToolsError("tools_source.type 'stdio' requires a 'command'")
        args = list(source.get("args") or [])
        # Merge the current environment with any per-source overrides so the
        # launched server inherits PATH etc. but can be given extra config.
        env = source.get("env")
        if env is not None:
            merged_env = dict(os.environ)
            merged_env.update({str(k): str(v) for k, v in env.items()})
            env = merged_env
        cwd = source.get("cwd")
        server_params = StdioServerParameters(
            command=command, args=args, env=env, cwd=cwd
        )
        try:
            return anyio.run(
                functools.partial(self._async_fetch_tools_stdio, server_params)
            )
        except McpToolsError:
            raise
        except Exception as e:
            raise McpToolsError(
                f"Failed to fetch tools from stdio server '{command}': {e}"
            ) from e

    async def _async_fetch_tools_stdio(
        self, server_params: StdioServerParameters
    ) -> list[mcp_types.Tool]:
        """Launch the local MCP server and list its tools over stdio."""
        async with stdio_client(server_params) as (reader, writer):
            async with ClientSession(reader, writer) as session:
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
