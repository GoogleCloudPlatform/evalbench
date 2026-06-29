"""Deterministic metrics scorer for an MCP endpoint's tool listing.

This is the *metrics* half of the MCP readability check. Unlike the LLM style
judge, it computes purely deterministic, model-independent numbers over the tools
returned by ``McpToolsGenerator``:

  - ``total_tools``: the number of tools exposed by the endpoint.
  - ``estimated_tokens``: a rough token footprint of the tool definitions,
    approximated as ``len(JSON(tool)) / 4`` summed across tools.
  - ``token_budget_used_percent``: that estimate as a percentage of the
    configured ``token_budget`` (``None`` when no positive budget is set).

It is kept separate from the LLM judge so the metric logic stays deterministic
and independently testable. The ``McpReadabilityOrchestrator`` calls ``score``
with the tools it gets back from the generator.
"""

from collections.abc import Sequence
import json
from typing import Any

# Rough chars-per-token heuristic for estimating a tool's token footprint.
_CHARS_PER_TOKEN = 4


class McpToolMetricsScorer:
    """Computes deterministic size/cost metrics for a list of MCP tools."""

    def __init__(self, config: dict | None = None):
        self.name = "mcp_tool_metrics"
        config = config or {}
        # A default token budget; a per-call value passed to score() wins.
        self.token_budget = config.get("token_budget")

    def score(
        self, tools: Sequence[Any], token_budget: int | None = None
    ) -> dict:
        """Compute metrics over ``tools``.

        Args:
          tools: The endpoint's tools (``mcp.types.Tool`` objects or plain
            dicts in the raw ``tools/list`` shape).
          token_budget: Overrides the budget from config when provided.

        Returns:
          ``{"total_tools", "estimated_tokens", "token_budget_used_percent"}``.
          ``token_budget_used_percent`` is ``None`` when no positive budget is
          configured.
        """
        budget = token_budget if token_budget is not None else self.token_budget

        total_tools = len(tools)
        total_chars = sum(len(self._to_json(tool)) for tool in tools)
        estimated_tokens = round(total_chars / _CHARS_PER_TOKEN)

        if budget and budget > 0:
            used_percent = round(estimated_tokens / budget * 100, 2)
        else:
            used_percent = None

        return {
            "total_tools": total_tools,
            "estimated_tokens": estimated_tokens,
            "token_budget_used_percent": used_percent,
        }

    @staticmethod
    def _to_json(tool: Any) -> str:
        """Serialize a tool to JSON for the size estimate.

        Handles both ``mcp.types.Tool`` (a pydantic model) and plain dicts.
        ``by_alias`` keeps the wire field names (e.g. ``inputSchema``) and
        ``exclude_none`` drops unset optionals, so the estimate reflects what an
        endpoint actually puts on the wire.
        """
        model_dump_json = getattr(tool, "model_dump_json", None)
        if callable(model_dump_json):
            try:
                return model_dump_json(by_alias=True, exclude_none=True)
            except TypeError:  # older/non-standard pydantic signatures
                return model_dump_json()
        return json.dumps(tool, default=str, sort_keys=True)
