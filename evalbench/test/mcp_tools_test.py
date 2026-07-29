"""Unit tests for the MCP tools generator (file source + URL/error handling).

The ``file`` source is exercised end-to-end (offline, deterministic). The
``http`` path is covered via a mocked async fetch so no network is required.
``stdio`` launches a real subprocess so it is left to integration testing.
"""

import json
import os
import tempfile
import unittest
from unittest.mock import patch

from mcp import types as mcp_types

from generators.models import mcp_client
from generators.models.mcp_client import McpToolsError
from generators.models.mcp_tools import McpToolsGenerator


_TOOLS_SPEC = {
    "tools": [
        {
            "name": "list_datasets",
            "description": "List all BigQuery datasets in the given project.",
            "inputSchema": {
                "type": "object",
                "properties": {
                    "project_id": {
                        "type": "string",
                        "description": "The Google Cloud project ID.",
                    }
                },
                "required": ["project_id"],
            },
        }
    ]
}


class McpToolsGeneratorTest(unittest.TestCase):

    def setUp(self):
        self.gen = McpToolsGenerator({})

    def _write_spec(self, obj) -> str:
        fd, path = tempfile.mkstemp(suffix=".json")
        with os.fdopen(fd, "w") as f:
            json.dump(obj, f)
        self.addCleanup(os.remove, path)
        return path

    # ---- file source --------------------------------------------------
    def test_file_source_returns_tools_and_man_page(self):
        path = self._write_spec(_TOOLS_SPEC)
        endpoint = {"tools_source": {"type": "file", "path": path}}
        tools, man_page = self.gen.fetch_tools(endpoint)
        self.assertEqual(len(tools), 1)
        self.assertIsInstance(tools[0], mcp_types.Tool)
        self.assertEqual(tools[0].name, "list_datasets")
        self.assertIn("TOOL: list_datasets", man_page)
        self.assertIn("project_id (string) [REQUIRED]", man_page)

    def test_file_missing_path_raises(self):
        endpoint = {"tools_source": {"type": "file"}}
        with self.assertRaises(McpToolsError):
            self.gen.fetch_tools(endpoint)

    def test_file_not_found_raises(self):
        endpoint = {"tools_source": {"type": "file", "path": "/no/such/file.json"}}
        with self.assertRaises(McpToolsError):
            self.gen.fetch_tools(endpoint)

    def test_file_wrong_shape_raises(self):
        path = self._write_spec({"not_tools": []})
        endpoint = {"tools_source": {"type": "file", "path": path}}
        with self.assertRaises(McpToolsError):
            self.gen.fetch_tools(endpoint)

    def test_file_empty_tools_raises(self):
        path = self._write_spec({"tools": []})
        endpoint = {"tools_source": {"type": "file", "path": path}}
        with self.assertRaises(McpToolsError):
            self.gen.fetch_tools(endpoint)

    # ---- source resolution / unknown type -----------------------------
    def test_unknown_source_type_raises(self):
        endpoint = {"tools_source": {"type": "carrier-pigeon"}}
        with self.assertRaises(McpToolsError):
            self.gen.fetch_tools(endpoint)

    # ---- url sanitization --------------------------------------------
    def test_sanitize_url_adds_scheme_and_mcp_suffix(self):
        self.assertEqual(
            mcp_client.sanitize_url("example.googleapis.com"),
            "https://example.googleapis.com/mcp",
        )

    def test_sanitize_url_localhost_uses_http(self):
        self.assertEqual(
            mcp_client.sanitize_url("localhost:8080"),
            "http://localhost:8080/mcp",
        )

    def test_sanitize_url_preserves_existing_mcp_suffix(self):
        self.assertEqual(
            mcp_client.sanitize_url("https://x.dev/mcp/"),
            "https://x.dev/mcp",
        )

    # ---- http source (mocked, no network) -----------------------------
    def test_http_source_uses_tools_source_url(self):
        captured = {}

        def fake_from_http(source):
            captured["url"] = source.get("url")
            return [
                mcp_types.Tool(name="t", description="d", inputSchema={})
            ]

        endpoint = {
            "tools_source": {
                "type": "http",
                "url": "https://svc.googleapis.com/mcp",
            },
        }
        with patch.object(self.gen, "_from_http", side_effect=fake_from_http):
            tools, man_page = self.gen.fetch_tools(endpoint)
        self.assertEqual(captured["url"], "https://svc.googleapis.com/mcp")
        self.assertEqual(len(tools), 1)
        self.assertIn("TOOL: t", man_page)

    def test_http_missing_url_raises(self):
        endpoint = {"tools_source": {"type": "http"}}
        with self.assertRaises(McpToolsError):
            self.gen.fetch_tools(endpoint)

    # ---- generate_internal passthrough --------------------------------
    def test_generate_internal_returns_man_page(self):
        path = self._write_spec(_TOOLS_SPEC)
        endpoint = {"tools_source": {"type": "file", "path": path}}
        out = self.gen.generate_internal(endpoint)
        self.assertIn("TOOL: list_datasets", out)

    def test_generate_internal_non_dict_returns_empty(self):
        self.assertEqual(self.gen.generate_internal("hello"), "")


if __name__ == "__main__":
    unittest.main()
