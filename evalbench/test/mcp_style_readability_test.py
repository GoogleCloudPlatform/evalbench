"""Unit tests for McpStyleReadabilityScorer generation and response parsing.

Generation covers the Gemini-3.x follow-ups: a high ``max_output_tokens`` is set
on the JSON-mode call, and a truncated response (``finish_reason ==
MAX_TOKENS``) raises a clear ``TruncatedResponseError`` instead of falling
through to a cryptic JSON parse failure. Parsing covers the per-tool findings
layout and the distinct-rule P0/P1/P2 counts derived from it.
"""

import json
import os
import tempfile
import types as pytypes
import unittest
from unittest.mock import patch

from google.genai.types import FinishReason

from scorers import mcp_style_readability
from scorers.mcp_readability_scoring import GENERAL
from scorers.mcp_style_readability import (
    McpStyleReadabilityScorer,
    TruncatedResponseError,
)


def _resp(finish_reason, text):
    """A minimal stand-in for a genai GenerateContentResponse."""
    candidate = pytypes.SimpleNamespace(finish_reason=finish_reason)
    return pytypes.SimpleNamespace(candidates=[candidate], text=text)


class _FakeGeminiModel:
    """Fake generator exposing the Gemini JSON-mode surface `_generate` uses."""

    def __init__(self, resp):
        self.client = object()  # non-None -> JSON-mode path is taken
        self._resp = resp
        self.last_config = None
        self.generate_called = False

    def _call_generate_content(self, contents, config):
        self.last_config = config
        return self._resp

    def generate(self, prompt):  # plain fallback path
        self.generate_called = True
        return '{"readability_score": 100, "findings": []}'


class GenerateTruncationTest(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.NamedTemporaryFile(
            mode="w", suffix=".md", delete=False
        )
        self._tmp.write("# style guide\n")
        self._tmp.close()

    def tearDown(self):
        os.unlink(self._tmp.name)

    def _scorer(self, model, config=None):
        cfg = {"model_config": "unused", "style_guide": self._tmp.name}
        cfg.update(config or {})
        with patch.object(
            mcp_style_readability, "get_generator", return_value=model
        ):
            return McpStyleReadabilityScorer(cfg, global_models=None)

    def test_sets_high_max_output_tokens_by_default(self):
        model = _FakeGeminiModel(_resp(FinishReason.STOP, '{"ok": true}'))
        scorer = self._scorer(model)
        out = scorer._generate("prompt")
        self.assertEqual(out, '{"ok": true}')
        self.assertEqual(
            model.last_config.max_output_tokens,
            mcp_style_readability._MAX_OUTPUT_TOKENS,
        )
        self.assertFalse(model.generate_called)

    def test_max_output_tokens_configurable(self):
        model = _FakeGeminiModel(_resp(FinishReason.STOP, '{"ok": true}'))
        scorer = self._scorer(model, {"max_output_tokens": 12345})
        scorer._generate("prompt")
        self.assertEqual(model.last_config.max_output_tokens, 12345)

    def test_truncation_raises_clear_error(self):
        model = _FakeGeminiModel(_resp(FinishReason.MAX_TOKENS, '{"readabil'))
        scorer = self._scorer(model)
        with self.assertRaises(TruncatedResponseError) as ctx:
            scorer._generate("prompt")
        msg = str(ctx.exception)
        self.assertIn("truncated", msg.lower())
        self.assertIn("max_output_tokens", msg)
        # Must NOT silently fall back to the plain generate() path.
        self.assertFalse(model.generate_called)

    def test_stop_returns_text(self):
        model = _FakeGeminiModel(_resp(FinishReason.STOP, '{"findings": []}'))
        scorer = self._scorer(model)
        self.assertEqual(scorer._generate("prompt"), '{"findings": []}')

    def test_generation_api_error_falls_back_to_plain_generate(self):
        # A genuine call failure (not truncation) still degrades gracefully.
        model = _FakeGeminiModel(_resp(FinishReason.STOP, "unused"))

        def boom(contents, config):
            raise RuntimeError("vertex unavailable")

        model._call_generate_content = boom
        scorer = self._scorer(model)
        out = scorer._generate("prompt")
        self.assertTrue(model.generate_called)
        self.assertIn("readability_score", out)


class ParsePerToolFindingsTest(unittest.TestCase):
    """`_parse` keeps one finding per tool but counts each rule once."""

    def _parse(self, findings):
        scorer = McpStyleReadabilityScorer.__new__(McpStyleReadabilityScorer)
        return scorer._parse(json.dumps({"findings": findings}))

    def test_same_rule_across_tools_counts_once_but_keeps_both(self):
        out = self._parse(
            [
                {
                    "severity": "P0",
                    "rule_id": "Avoid complex parameters",
                    "tool": "create_instance",
                    "message": "pscInstanceConfig is deeply nested.",
                },
                {
                    "severity": "P0",
                    "rule_id": "Avoid complex parameters",
                    "tool": "update_instance",
                    "message": "settingsConfig is deeply nested.",
                },
            ]
        )
        # One rule broken by two tools: one P0, but each tool keeps its own
        # finding and its own tool-specific message.
        self.assertEqual(out["p0_issues"], 1)
        self.assertEqual(len(out["findings"]), 2)
        self.assertEqual(
            [t["tool"] for t in out["findings_by_tool"]],
            ["create_instance", "update_instance"],
        )
        self.assertIn(
            "settingsConfig",
            out["findings_by_tool"][1]["findings"][0]["message"],
        )

    def test_distinct_rules_are_counted_separately(self):
        out = self._parse(
            [
                {"severity": "P0", "rule_id": "Safe Pagination", "tool": "a"},
                {"severity": "P0", "rule_id": "Be Consistent", "tool": "a"},
                {"severity": "P1", "rule_id": "Use Enums", "tool": "b"},
            ]
        )
        self.assertEqual(out["p0_issues"], 2)
        self.assertEqual(out["p1_issues"], 1)

    def test_toolless_finding_lands_in_all_tools_bucket(self):
        out = self._parse(
            [{"severity": "P1", "rule_id": "Missing capability", "tool": ""}]
        )
        self.assertEqual(out["findings"][0]["tool"], "")
        self.assertEqual(out["findings_by_tool"][0]["tool"], GENERAL)

    def test_ruleless_findings_each_count(self):
        # Without a rule_id there is nothing to collapse on, so both count.
        out = self._parse(
            [
                {"severity": "P2", "tool": "a", "title": "no rule"},
                {"severity": "P2", "tool": "b", "title": "no rule"},
            ]
        )
        self.assertEqual(out["p2_issues"], 2)


if __name__ == "__main__":
    unittest.main()
