"""Unit tests for the deterministic MCP tool metrics scorer."""

import json
import unittest

from mcp import types as mcp_types

from scorers.mcp_tool_metrics import McpToolMetricsScorer


def _tool(name, description, schema=None):
    return mcp_types.Tool(
        name=name, description=description, inputSchema=schema or {"type": "object"}
    )


class McpToolMetricsScorerTest(unittest.TestCase):

    def setUp(self):
        self.scorer = McpToolMetricsScorer()

    # ---- total_tools --------------------------------------------------
    def test_total_tools_counts_entries(self):
        tools = [_tool("a", "A."), _tool("b", "B."), _tool("c", "C.")]
        self.assertEqual(self.scorer.score(tools)["total_tools"], 3)

    def test_empty_tools(self):
        result = self.scorer.score([])
        self.assertEqual(result["total_tools"], 0)
        self.assertEqual(result["estimated_tokens"], 0)
        self.assertIsNone(result["token_budget_used_percent"])

    # ---- estimated_tokens (dict input -> exact, computable) -----------
    def test_estimated_tokens_matches_json_quarter_for_dicts(self):
        tools = [
            {"name": "x", "description": "hello", "inputSchema": {"type": "object"}},
            {"name": "y", "description": "world", "inputSchema": {"type": "string"}},
        ]
        expected_chars = sum(
            len(json.dumps(t, default=str, sort_keys=True)) for t in tools
        )
        result = self.scorer.score(tools)
        self.assertEqual(result["estimated_tokens"], round(expected_chars / 4))

    def test_estimated_tokens_positive_for_real_tools(self):
        result = self.scorer.score([_tool("a", "A tool that does things.")])
        self.assertGreater(result["estimated_tokens"], 0)

    def test_more_tools_means_more_tokens(self):
        one = self.scorer.score([_tool("a", "A.")])["estimated_tokens"]
        two = self.scorer.score([_tool("a", "A."), _tool("b", "B.")])[
            "estimated_tokens"
        ]
        self.assertGreater(two, one)

    # ---- token_budget_used_percent -----------------------------------
    def test_budget_percent_computed(self):
        tools = [{"name": "x", "description": "d"}]
        est = self.scorer.score(tools)["estimated_tokens"]
        budget = 1000
        result = self.scorer.score(tools, token_budget=budget)
        self.assertEqual(
            result["token_budget_used_percent"], round(est / budget * 100, 2)
        )

    def test_no_budget_yields_none_percent(self):
        result = self.scorer.score([_tool("a", "A.")])
        self.assertIsNone(result["token_budget_used_percent"])

    def test_zero_budget_yields_none_percent(self):
        result = self.scorer.score([_tool("a", "A.")], token_budget=0)
        self.assertIsNone(result["token_budget_used_percent"])

    def test_empty_tools_with_budget_is_zero_percent(self):
        result = self.scorer.score([], token_budget=1000)
        self.assertEqual(result["token_budget_used_percent"], 0.0)

    # ---- config vs per-call budget -----------------------------------
    def test_config_budget_used_when_no_override(self):
        scorer = McpToolMetricsScorer({"token_budget": 1000})
        tools = [{"name": "x", "description": "d"}]
        est = scorer.score(tools)["estimated_tokens"]
        self.assertEqual(
            scorer.score(tools)["token_budget_used_percent"],
            round(est / 1000 * 100, 2),
        )

    def test_per_call_budget_overrides_config(self):
        scorer = McpToolMetricsScorer({"token_budget": 1000})
        tools = [{"name": "x", "description": "d"}]
        est = scorer.score(tools)["estimated_tokens"]
        result = scorer.score(tools, token_budget=2000)
        self.assertEqual(
            result["token_budget_used_percent"], round(est / 2000 * 100, 2)
        )


if __name__ == "__main__":
    unittest.main()
