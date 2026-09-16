"""Client for MCP ``tools/list`` over Streamable HTTP or stdio.

Transport plus the auth translation for the ``mcp_servers`` config schema
(``authProviderType``/``headers``). Callers own the config that produced the
URL or command.
"""

import functools

import anyio

from mcp import types as mcp_types
from mcp import StdioServerParameters
from mcp.client.session import ClientSession
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamablehttp_client


class McpToolsError(Exception):
    """Raised when a tools spec cannot be fetched or parsed."""


def _format_error(e: BaseException) -> str:
    """Unpack ExceptionGroup/TaskGroup leaf errors for error messages."""
    if hasattr(e, "exceptions") and getattr(e, "exceptions"):
        return f"{e} ({', '.join(map(_format_error, e.exceptions))})"
    return f"{type(e).__name__}: {e}" if str(e) else type(e).__name__


def auth_headers(server_config: dict) -> dict | None:
    """Build request headers for an MCP server (configured headers + auth).

    ``authProviderType: google_credentials`` mints a bearer token from ADC for
    the server's OAuth scopes. Returns ``None`` when the server needs no
    headers.
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


def fetch_tools_http(
    raw_url: str, headers: dict | None = None, timeout: int = 30
) -> list[mcp_types.Tool]:
    """``tools/list`` over Streamable HTTP."""
    url = (raw_url or "").strip()
    try:
        return anyio.run(
            functools.partial(_async_fetch_http, url, headers, timeout)
        )
    except McpToolsError:
        raise
    except Exception as e:
        err = _format_error(e)
        raise McpToolsError(
            f"Failed to fetch tools from {url}: {err}"
        ) from e


def fetch_tools_stdio(
    command: str, args: list | None = None, env: dict | None = None,
    cwd: str | None = None,
) -> list[mcp_types.Tool]:
    """``tools/list`` from a locally launched MCP server over stdio."""
    server_params = StdioServerParameters(
        command=command, args=list(args or []), env=env, cwd=cwd
    )
    try:
        return anyio.run(
            functools.partial(_async_fetch_stdio, server_params)
        )
    except McpToolsError:
        raise
    except Exception as e:
        err = _format_error(e)
        raise McpToolsError(
            f"Failed to fetch tools from stdio server '{command}': {err}"
        ) from e


async def _async_fetch_http(
    url: str, headers: dict | None, timeout: int
) -> list[mcp_types.Tool]:
    async with streamablehttp_client(
        url, headers=headers, timeout=timeout
    ) as streams:
        reader, writer, _ = streams
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools_response = await session.list_tools()
    return list(tools_response.tools)


async def _async_fetch_stdio(
    server_params: StdioServerParameters,
) -> list[mcp_types.Tool]:
    async with stdio_client(server_params) as (reader, writer):
        async with ClientSession(reader, writer) as session:
            await session.initialize()
            tools_response = await session.list_tools()
    return list(tools_response.tools)
