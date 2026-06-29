"""Render MCP tools into a human-readable "man page".

This is the *rendering* half of the MCP readability check. It takes a sequence of
MCP tools (as returned by ``tools/list``) and produces a single man-page style
string that the readability scorer feeds to an LLM judge.

The renderer:
  - resolves JSON-Schema ``$ref`` / ``$defs`` references,
  - flattens nested parameters into dotted paths,
  - marks each parameter ``REQUIRED`` / ``OPTIONAL``,
  - renders structured ``enum`` and ``default`` values so style rules that key
    off them (e.g. "Use Enums") are judgeable from the markup,
  - guards against infinite recursion in self-referential schemas.

Scope note: this module *only* renders the man page. Deterministic metrics such
as tool count, estimated tokens, and token-budget usage are computed by a
separate scorer, not here -- the generator's job is to produce the artifact, and
the scorers compute metrics over it.
"""

from collections.abc import Mapping, Sequence, Set
import json
import textwrap
from typing import Any

from mcp import types as mcp_types


def _wrap_text(text: str, indent: str) -> list[str]:
    """Wraps text preserving empty lines and applying indentation."""
    wrapped = []
    for line in (text or "").splitlines():
        line = line.strip()
        if not line:
            if wrapped:  # Don't add leading empty lines.
                wrapped.append("")
        else:
            wrapped.extend(
                textwrap.wrap(
                    line, width=80, initial_indent=indent, subsequent_indent=indent
                )
            )
    while wrapped and not wrapped[-1]:
        wrapped.pop()
    return wrapped


def _value_indent(level: int) -> str:
    """Indent used for sub-lines (description/enum/default) at a given level.

    The base sub-line sits 6 spaces in, and each nesting level adds 3 more.
    """
    return " " * (6 + 3 * level)


def _format_enum_default(schema: Mapping[str, Any], level: int) -> list[str]:
    """Render ``enum`` / ``default`` values for a property, if present."""
    lines: list[str] = []
    indent = _value_indent(level)
    if "enum" in schema and isinstance(schema["enum"], (list, tuple)):
        values = ", ".join(json.dumps(v, default=str) for v in schema["enum"])
        lines.extend(_wrap_text(f"enum: [{values}]", indent))
    if "default" in schema:
        lines.extend(
            _wrap_text(f"default: {json.dumps(schema['default'], default=str)}", indent)
        )
    return lines


def _resolve_ref(
    schema: Mapping[str, Any], defs: Mapping[str, Any]
) -> tuple[Mapping[str, Any], str | None]:
    """Resolves JSON schema references.

    Args:
      schema: The current JSON schema dictionary.
      defs: A dictionary of schema definitions where '$ref' can be resolved.

    Returns:
      A tuple (resolved_schema, ref_name), where `resolved_schema` is the
      resolved schema dictionary (or the original schema if no '$ref' is present
      or resolvable), and `ref_name` is the name of the resolved reference (e.g.,
      the part after '#/$defs/'), or None if no reference was resolved.
    """
    if "$ref" in schema:
        ref_path = schema["$ref"]
        if ref_path.startswith("#/$defs/"):
            ref_name = ref_path.split("/")[-1]
            if ref_name in defs:
                return defs[ref_name], ref_name
    return schema, None


def _format_type(schema: Mapping[str, Any]) -> str:
    """Formats a JSON schema type representation."""
    schema_type = schema.get("type", "any")
    if isinstance(schema_type, list):
        return " | ".join(str(t) for t in schema_type)
    return str(schema_type)


def _format_header(
    prop_name: str,
    prop_type: str,
    req_str: str,
    desc: str,
    full_path: str,
    level: int,
) -> list[str]:
    """Formats a property's header line and description based on nesting level."""
    lines = []
    if level == 0:
        lines.append(f"  {prop_name} ({prop_type}) [{req_str}]")
    elif level == 1:
        lines.append(f"      -> {full_path} ({prop_type}) [{req_str}]")
    else:
        lines.append(f"{' ' * (4 + 3 * level)}* {prop_name} ({prop_type})")
    if desc:
        lines.extend(_wrap_text(desc, _value_indent(level)))
    return lines


def _format_recursive(level: int) -> list[str]:
    """Formats a recursive reference notice for array items at the given level."""
    return _wrap_text("[Recursive Reference]", _value_indent(level))


def _format_obj(
    schema: Mapping[str, Any],
    defs: Mapping[str, Any],
    full_path: str,
    level: int,
    visited: Set[str],
) -> list[str]:
    """Formats an object property by recursively traversing its children."""
    lines = _format_props(
        schema,
        defs=defs,
        path_prefix=full_path,
        level=level + 1,
        visited=visited,
    )
    if lines and lines[-1]:
        lines.append("")
    return lines


def _format_array(
    schema: Mapping[str, Any],
    defs: Mapping[str, Any],
    full_path: str,
    level: int,
    visited: Set[str],
) -> list[str]:
    """Formats an array property by traversing its items schema."""
    # Resolve the items schema to check if it points to a reference definition.
    schema, ref = _resolve_ref(schema["items"], defs)
    is_recursive = False
    if ref:
        # Check if this reference name has already been encountered higher up in
        # the current traversal path to prevent infinite recursion.
        if ref in visited:
            is_recursive = True
        else:
            visited = visited | {ref}

    if not is_recursive and schema.get("type") == "object":
        return _format_props(
            schema,
            defs=defs,
            path_prefix=full_path + "[]",
            level=level + 1,
            visited=visited,
        )
    if is_recursive:
        return _format_recursive(level)
    return []


def _format_props(
    schema: Mapping[str, Any],
    *,
    defs: Mapping[str, Any] | None = None,
    path_prefix: str = "",
    level: int = 0,
    visited: Set[str] | None = None,
) -> list[str]:
    """Recursively formats JSON schema properties into man-page parameter lines."""
    lines = []
    if defs is None:
        defs = schema.get("$defs", {}) or schema.get("definitions", {})
    visited = visited if visited is not None else set()

    schema, _ = _resolve_ref(schema, defs)
    properties = schema.get("properties", {})
    required_fields = set(schema.get("required", []))

    for prop_name, prop_schema in properties.items():
        prop_schema, ref_name = _resolve_ref(prop_schema, defs)
        is_recursive = ref_name in visited if ref_name else False
        prop_visited = (
            visited | {ref_name} if ref_name and not is_recursive else visited
        )

        is_required = prop_name in required_fields
        req_str = "REQUIRED" if is_required else "OPTIONAL"
        prop_type = _format_type(prop_schema)

        desc = prop_schema.get("description", "").strip()
        if is_recursive:
            desc = f"[Recursive Reference] {desc}".strip()

        full_path = f"{path_prefix}.{prop_name}" if path_prefix else prop_name

        lines.extend(
            _format_header(prop_name, prop_type, req_str, desc, full_path, level)
        )
        lines.extend(_format_enum_default(prop_schema, level))

        if not is_recursive:
            if prop_type == "object" and "properties" in prop_schema:
                lines.extend(
                    _format_obj(prop_schema, defs, full_path, level, prop_visited)
                )
            elif prop_type == "array" and "items" in prop_schema:
                lines.extend(
                    _format_array(prop_schema, defs, full_path, level, prop_visited)
                )

        if level == 0:
            if lines and lines[-1]:
                lines.append("")

    return lines


def format_tools_to_man_page(tools: Sequence[mcp_types.Tool]) -> str:
    """Formats a sequence of MCP tools into a man-page style string.

    Args:
      tools: Sequence of ``mcp.types.Tool`` objects.

    Returns:
      The formatted man-page markup. Returns ``"No tools available."`` for an
      empty sequence.
    """
    if not tools:
        return "No tools available."

    lines = []
    for tool in tools:
        lines.append("=" * 80)
        lines.append(f"TOOL: {tool.name}")
        lines.append("=" * 80)
        lines.append("DESCRIPTION:")

        desc = tool.description or "No description provided."
        lines.extend(_wrap_text(desc, "  "))

        lines.append("\nPARAMETERS:")
        if tool.inputSchema and tool.inputSchema.get("properties"):
            schema_lines = _format_props(tool.inputSchema)
            lines.extend(schema_lines)
        else:
            lines.append("  None\n")

    return "\n".join(lines).strip()
