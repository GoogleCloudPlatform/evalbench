"""Unit tests for McpStyleReadabilityScorer._generate token/truncation handling.

These cover the Gemini-3.x follow-ups: a high ``max_output_tokens`` is set on the
JSON-mode call, and a truncated response (``finish_reason == MAX_TOKENS``) raises
a clear ``TruncatedResponseError`` instead of falling through to a cryptic JSON
parse failure.
"""

import os
import tempfile
import types as pytypes
import unittest
from unittest.mock import patch

from google.genai.types import FinishReason

from scorers import mcp_style_readability
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


if __name__ == "__main__":
    unittest.main()
