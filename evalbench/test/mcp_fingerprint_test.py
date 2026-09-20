"""Unit tests for the MCP readability tool fingerprints.

A fingerprint that is stable when it should not be would misreport an edited
tool as unchanged, and one that churns would report every run as a rewrite.
"""

import unittest

from mcp import types as mcp_types

from scorers.mcp_fingerprint import tool_fingerprints


def _tool(name, description="Does a thing.", schema=None):
    return mcp_types.Tool(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object"},
    )


class ToolFingerprintsTest(unittest.TestCase):

    def test_identical_tools_fingerprint_identically(self):
        self.assertEqual(
            tool_fingerprints([_tool("a")]), tool_fingerprints([_tool("a")])
        )

    def test_description_change_changes_the_fingerprint(self):
        before = tool_fingerprints([_tool("a", "Old.")])
        after = tool_fingerprints([_tool("a", "New.")])
        self.assertNotEqual(before["a"], after["a"])

    def test_schema_change_changes_the_fingerprint(self):
        before = tool_fingerprints([_tool("a")])
        after = tool_fingerprints(
            [_tool("a", schema={"type": "object",
                                "properties": {"x": {"type": "string"}}})]
        )
        self.assertNotEqual(before["a"], after["a"])

    def test_other_tools_do_not_affect_a_tool_fingerprint(self):
        alone = tool_fingerprints([_tool("a")])
        together = tool_fingerprints([_tool("a"), _tool("b")])
        self.assertEqual(alone["a"], together["a"])

    def test_duplicate_names_keep_the_first_declaration(self):
        fps = tool_fingerprints([_tool("a", "First."), _tool("a", "Second.")])
        self.assertEqual(list(fps), ["a"])
        self.assertEqual(fps["a"], tool_fingerprints([_tool("a", "First.")])["a"])


if __name__ == "__main__":
    unittest.main()
