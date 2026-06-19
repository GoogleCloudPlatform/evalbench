"""MCP toolset client + schema renderer.

A pair of tightly-coupled utilities used by the compliance-check evaluator:

- ``McpToolsetClient`` connects to an MCP server (stdio or HTTP) and pulls
  the tool schema list. Pure data fetch — no scoring, no auth. HTTP
  endpoints are assumed to be directly reachable; if you need auth, add it
  to your URL or run a reverse proxy that injects credentials.
- ``schema_to_yaml`` renders one tool dict (as the client emits) into a
  compact, human-friendly YAML. That YAML is the single source of truth
  consumed by both the LLM judge prompt and the deterministic token
  counter, so it must be lossless against the original JSON Schema while
  being more compact and readable than the raw form.

Both live together because every consumer needs both: fetch returns dicts,
score/judge needs YAML.
"""

from __future__ import annotations

import asyncio
import logging
from collections import OrderedDict
from typing import Any

import yaml
from mcp import ClientSession
from mcp.client.stdio import StdioServerParameters, stdio_client
from mcp.client.streamable_http import streamablehttp_client


# ============================================================
# Toolset client
# ============================================================


class McpProbeError(Exception):
    """Raised when the toolset client fails to connect or list tools."""


class McpToolsetClient:
    """Fetches ``tools/list`` from an MCP endpoint.

    Endpoint config shape::

        stdio:  {transport: stdio, command: ..., args: [...], env: {...}, cwd: ...}
        http:   {transport: http,  url: ..., headers: {...}}
    """

    def __init__(self, endpoint_cfg: dict[str, Any]):
        self.cfg = endpoint_cfg
        self.endpoint_id = endpoint_cfg.get("id", "<unknown>")
        transport = endpoint_cfg.get("transport")
        if transport not in ("stdio", "http"):
            raise ValueError(
                f"Endpoint {self.endpoint_id}: unsupported transport "
                f"{transport!r} (expected 'stdio' or 'http')"
            )
        self.transport = transport

    def list_tools_sync(self, timeout_s: float = 30.0) -> list[dict[str, Any]]:
        """Synchronous wrapper that returns tools as plain dicts.

        Spins up its own event loop, so this is safe to call from the
        MPRunner thread pool.
        """
        try:
            return asyncio.run(self._list_tools_async(timeout_s))
        except Exception as e:
            raise McpProbeError(
                f"Endpoint {self.endpoint_id}: {type(e).__name__}: {e}"
            ) from e

    async def _list_tools_async(self, timeout_s: float) -> list[dict[str, Any]]:
        if self.transport == "stdio":
            return await asyncio.wait_for(self._list_stdio(), timeout=timeout_s)
        return await asyncio.wait_for(self._list_http(), timeout=timeout_s)

    async def _list_stdio(self) -> list[dict[str, Any]]:
        params = StdioServerParameters(
            command=self.cfg["command"],
            args=self.cfg.get("args", []),
            env=self.cfg.get("env"),
            cwd=self.cfg.get("cwd"),
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [_tool_to_dict(t) for t in result.tools]

    async def _list_http(self) -> list[dict[str, Any]]:
        headers = dict(self.cfg.get("headers") or {})
        async with streamablehttp_client(
            url=self.cfg["url"],
            headers=headers,
        ) as (read, write, _get_session_id):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await session.list_tools()
                return [_tool_to_dict(t) for t in result.tools]


def _tool_to_dict(tool: Any) -> dict[str, Any]:
    # mcp.types.Tool is a pydantic model; model_dump gives us a clean dict.
    d = tool.model_dump() if hasattr(tool, "model_dump") else dict(tool)
    return {
        "name": d.get("name"),
        "description": d.get("description") or "",
        "inputSchema": d.get("inputSchema") or {"type": "object", "properties": {}},
        "outputSchema": d.get("outputSchema"),
        "annotations": d.get("annotations"),
    }


# ============================================================
# Schema → YAML renderer
# ============================================================


_PARAM_KEY_ORDER = (
    "type",
    "required",
    "description",
    "enum",
    "default",
    "format",
    "pattern",
    "minimum",
    "maximum",
    "items",
    "properties",
    "examples",
)


def schema_to_yaml(tool: dict[str, Any]) -> str:
    """Render one tool dict (as produced by McpToolsetClient) to YAML.

    Transforms applied:

    - Flatten ``properties`` + ``required`` into a per-param table.
    - Pull ``enum`` / ``default`` / ``format`` / ``pattern`` / ``examples``
      / ``minimum`` / ``maximum`` up to first-class fields when present.
    - Resolve ``$ref`` one level deep against the schema's local ``$defs``.
    - Stable key order so two YAMLs of the same schema diff cleanly.
    """
    name = tool.get("name") or "<unnamed>"
    description = (tool.get("description") or "").strip()
    input_schema = tool.get("inputSchema") or {}
    output_schema = tool.get("outputSchema") or None

    defs = input_schema.get("$defs") or {}
    properties = input_schema.get("properties") or {}
    required = set(input_schema.get("required") or [])

    params = OrderedDict()
    for pname, pschema in properties.items():
        params[pname] = _normalize_param(pschema, pname in required, defs)

    doc = OrderedDict()
    doc["tool"] = name
    if description:
        doc["description"] = _LiteralStr(description)
    if params:
        doc["parameters"] = params
    if output_schema:
        out_summary = _summarize_output(output_schema)
        if out_summary:
            doc["returns"] = out_summary

    return _dump_yaml(doc)


def _normalize_param(
    pschema: dict[str, Any], is_required: bool, defs: dict[str, Any]
) -> OrderedDict:
    resolved = _resolve_ref(pschema, defs)
    out = OrderedDict()
    t = resolved.get("type")
    if t is not None:
        out["type"] = t
    out["required"] = is_required
    desc = (resolved.get("description") or "").strip()
    if desc:
        out["description"] = _LiteralStr(desc) if "\n" in desc else desc
    for key in ("enum", "default", "format", "pattern", "minimum", "maximum", "examples"):
        if key in resolved and resolved[key] is not None:
            out[key] = resolved[key]
    if t == "array" and "items" in resolved:
        out["items"] = _normalize_param(resolved["items"], False, defs)
    if t == "object" and "properties" in resolved:
        nested_required = set(resolved.get("required") or [])
        nested = OrderedDict()
        for k, v in resolved["properties"].items():
            nested[k] = _normalize_param(v, k in nested_required, defs)
        out["properties"] = nested
    # Stable key ordering for diff-friendliness.
    return OrderedDict(
        (k, out[k]) for k in _PARAM_KEY_ORDER if k in out
    ) | OrderedDict((k, v) for k, v in out.items() if k not in _PARAM_KEY_ORDER)


def _resolve_ref(schema: dict[str, Any], defs: dict[str, Any]) -> dict[str, Any]:
    """One-level $ref resolution against local $defs (no recursion)."""
    ref = schema.get("$ref")
    if not ref or not isinstance(ref, str):
        return schema
    parts = ref.split("/")
    if len(parts) >= 3 and parts[0] == "#" and parts[1] in ("$defs", "definitions"):
        return defs.get(parts[2], schema)
    return schema


def _summarize_output(output_schema: dict[str, Any]) -> Any:
    """Output schemas are typically huge response envelopes.

    Keep just the top-level type + description so the judge sees what's
    returned without drowning in nested definitions.
    """
    desc = (output_schema.get("description") or "").strip()
    t = output_schema.get("type")
    out = OrderedDict()
    if t:
        out["type"] = t
    if desc:
        out["description"] = _LiteralStr(desc) if "\n" in desc else desc
    return out or None


class _LiteralStr(str):
    """Marker so PyYAML renders the string in block-literal (``|``) form."""


def _literal_str_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style="|")


def _ordereddict_representer(dumper, data):
    return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())


class _ReadableDumper(yaml.SafeDumper):
    pass


_ReadableDumper.add_representer(_LiteralStr, _literal_str_representer)
_ReadableDumper.add_representer(OrderedDict, _ordereddict_representer)


def _dump_yaml(doc: Any) -> str:
    return yaml.dump(
        doc,
        Dumper=_ReadableDumper,
        sort_keys=False,
        default_flow_style=False,
        allow_unicode=True,
        width=100,
    )


# Convenience for quick smoke checks:
#   uv run python -m generators.models.mcp_toolset https://sqladmin.googleapis.com/mcp
if __name__ == "__main__":
    import sys

    logging.basicConfig(level=logging.INFO)
    if len(sys.argv) < 2:
        print("usage: mcp_toolset.py <http-url-or-stdio-command> [args...]")
        sys.exit(2)
    arg = sys.argv[1]
    if arg.startswith(("http://", "https://")):
        cfg = {
            "id": "smoke",
            "transport": "http",
            "url": arg,
        }
    else:
        cfg = {
            "id": "smoke",
            "transport": "stdio",
            "command": arg,
            "args": sys.argv[2:],
        }
    tools = McpToolsetClient(cfg).list_tools_sync()
    print(f"=== {len(tools)} tools fetched ===\n")
    for t in tools[:3]:
        print(f"--- {t['name']} ---")
        print(schema_to_yaml(t))
