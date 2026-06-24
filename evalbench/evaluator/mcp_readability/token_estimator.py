"""Token estimation for an MCP tools spec.

Uses a lightweight heuristic so the feature carries no extra dependency. The
estimate intentionally errs slightly high (tool schemas are JSON-ish and
tokenize denser than prose). If exact counts are needed, swap ``estimate_tokens``
for ``genai_client.models.count_tokens(...)`` — the call sites only depend on the
function signature.
"""

import math


def estimate_tokens(text: str) -> int:
    """Estimate the number of LLM tokens in ``text``.

    Heuristic: roughly 4 characters per token, with a small floor so a non-empty
    spec never estimates to zero.
    """
    if not text:
        return 0
    # ~4 chars/token is the common rule of thumb for English + JSON.
    return max(1, math.ceil(len(text) / 4))


def token_budget_used_percent(estimated_tokens: int, token_budget: int) -> float:
    """Percent of the token budget consumed, rounded to 2 decimals.

    Returns ``0.0`` when the budget is missing or non-positive to avoid
    divide-by-zero blowing up an otherwise-successful check.
    """
    if not token_budget or token_budget <= 0:
        return 0.0
    return round(100.0 * estimated_tokens / token_budget, 2)
