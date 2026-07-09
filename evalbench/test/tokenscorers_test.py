"""Tests for the tokens_processed and effective_billed_tokens scorers."""
import json

from scorers.tokensprocessed import TokensProcessed
from scorers.effectivebilledtokens import EffectiveBilledTokens


def _history(tokens: dict) -> str:
    """Builds a one-turn conversation-history JSON string carrying a single
    model token bucket, matching the shape scorers read
    (turn -> agent JSON -> stats.models.<m>.tokens)."""
    agent = json.dumps({"stats": {"models": {"claude-opus-4-8": {"tokens": tokens}}}})
    return json.dumps([{"user": "hi", "agent": agent}])


# Full bucket as emitted by claude_code.py. `prompt` duplicates `input` and
# must NOT be summed.
FULL_TOKENS = {
    "input": 1000,
    "prompt": 1000,
    "candidates": 200,
    "total": 1200,
    "cached": 30000,
    "cache_creation": 5000,
    "thoughts": 0,
    "tool": 0,
}

# Common no-op args for the compare() signature.
_ARGS = ("nl", "golden", "qtype", None, "", "", "gen", None)


def _compare(scorer, generated_eval_result, generated_error=""):
    return scorer.compare(*_ARGS, generated_eval_result, generated_error)


def test_tokens_processed_sums_all_tiers_unweighted():
    scorer = TokensProcessed({})
    score, _ = _compare(scorer, _history(FULL_TOKENS))
    # input + candidates + cached + cache_creation (prompt excluded).
    assert score == 1000 + 200 + 30000 + 5000


def test_effective_billed_tokens_default_weights():
    scorer = EffectiveBilledTokens({})
    score, _ = _compare(scorer, _history(FULL_TOKENS))
    expected = (
        1000 * 1.0      # input
        + 30000 * 0.1   # cached
        + 5000 * 1.25   # cache_creation
        + 200 * 5.0     # output
    )
    assert score == expected


def test_effective_billed_tokens_config_override():
    scorer = EffectiveBilledTokens({
        "input_weight": 1.0,
        "cached_weight": 0.0,
        "cache_write_weight": 1.0,
        "output_weight": 1.0,
    })
    score, _ = _compare(scorer, _history(FULL_TOKENS))
    # cached zeroed out; everything else at 1.0 -> input + cache_creation + output.
    assert score == 1000 + 5000 + 200


def test_missing_cache_keys_degrade_to_input_plus_output():
    minimal = {"input": 100, "candidates": 50, "total": 150}
    assert _compare(TokensProcessed({}), _history(minimal))[0] == 150
    # effective: input*1 + output*5 (no cache tiers present).
    assert _compare(EffectiveBilledTokens({}), _history(minimal))[0] == 100 + 50 * 5.0


def test_generation_error_returns_zero():
    for scorer in (TokensProcessed({}), EffectiveBilledTokens({})):
        assert _compare(scorer, _history(FULL_TOKENS), "boom")[0] == 0.0


def test_empty_history_returns_zero():
    for scorer in (TokensProcessed({}), EffectiveBilledTokens({})):
        assert _compare(scorer, "")[0] == 0.0
