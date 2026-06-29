"""Unit tests for the MCP man-page formatter.

These exercise the rendering logic in isolation -- no network, no LLM, no auth --
covering simple/nested/array params, $ref/$defs resolution, enum/default
rendering, the required/optional marker, and the recursion guard.
"""

import unittest

from mcp import types as mcp_types

from generators.models.mcp_tool_formatter import format_tools_to_man_page


def _tool(name, description, schema):
    return mcp_types.Tool(name=name, description=description, inputSchema=schema)


class FormatToolsToManPageTest(unittest.TestCase):

    def test_empty_tools(self):
        self.assertEqual(format_tools_to_man_page([]), "No tools available.")

    def test_tool_with_no_parameters(self):
        out = format_tools_to_man_page(
            [_tool("ping", "Health check.", {"type": "object"})]
        )
        self.assertIn("TOOL: ping", out)
        self.assertIn("Health check.", out)
        self.assertIn("PARAMETERS:", out)
        self.assertIn("None", out)

    def test_simple_required_and_optional(self):
        schema = {
            "type": "object",
            "properties": {
                "query": {"type": "string", "description": "The search query."},
                "limit": {"type": "integer", "description": "Max results."},
            },
            "required": ["query"],
        }
        out = format_tools_to_man_page([_tool("search", "Search docs.", schema)])
        self.assertIn("query (string) [REQUIRED]", out)
        self.assertIn("The search query.", out)
        self.assertIn("limit (integer) [OPTIONAL]", out)

    def test_enum_and_default_rendering(self):
        schema = {
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["fast", "slow"],
                    "default": "fast",
                    "description": "Execution mode.",
                }
            },
        }
        out = format_tools_to_man_page([_tool("run", "Run it.", schema)])
        self.assertIn('enum: ["fast", "slow"]', out)
        self.assertIn('default: "fast"', out)

    def test_nested_object_uses_dotted_path(self):
        schema = {
            "type": "object",
            "properties": {
                "filter": {
                    "type": "object",
                    "description": "Filter spec.",
                    "properties": {
                        "field": {"type": "string", "description": "Field name."}
                    },
                    "required": ["field"],
                }
            },
        }
        out = format_tools_to_man_page([_tool("list", "List rows.", schema)])
        self.assertIn("filter (object)", out)
        self.assertIn("filter.field (string) [REQUIRED]", out)

    def test_ref_defs_resolution(self):
        schema = {
            "type": "object",
            "$defs": {
                "Address": {
                    "type": "object",
                    "properties": {
                        "city": {"type": "string", "description": "City name."}
                    },
                }
            },
            "properties": {"home": {"$ref": "#/$defs/Address"}},
        }
        out = format_tools_to_man_page([_tool("save", "Save user.", schema)])
        self.assertIn("home (object)", out)
        self.assertIn("home.city (string)", out)

    def test_array_of_objects(self):
        schema = {
            "type": "object",
            "properties": {
                "rows": {
                    "type": "array",
                    "description": "Rows to insert.",
                    "items": {
                        "type": "object",
                        "properties": {
                            "id": {"type": "string", "description": "Row id."}
                        },
                    },
                }
            },
        }
        out = format_tools_to_man_page([_tool("insert", "Insert.", schema)])
        self.assertIn("rows (array)", out)
        self.assertIn("rows[].id (string)", out)

    def test_recursive_schema_does_not_loop(self):
        schema = {
            "type": "object",
            "$defs": {
                "Node": {
                    "type": "object",
                    "properties": {
                        "value": {"type": "string", "description": "Value."},
                        "children": {
                            "type": "array",
                            "items": {"$ref": "#/$defs/Node"},
                        },
                    },
                }
            },
            "properties": {"root": {"$ref": "#/$defs/Node"}},
        }
        out = format_tools_to_man_page([_tool("tree", "Build tree.", schema)])
        self.assertIn("root (object)", out)
        self.assertIn("[Recursive Reference]", out)

    def test_multiple_tools_all_rendered(self):
        out = format_tools_to_man_page(
            [
                _tool("a", "Tool A.", {"type": "object"}),
                _tool("b", "Tool B.", {"type": "object"}),
            ]
        )
        self.assertIn("TOOL: a", out)
        self.assertIn("TOOL: b", out)


if __name__ == "__main__":
    unittest.main()
