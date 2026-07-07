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
and independently testable. It is a plug-and-play mcp_readability scorer (see
``scorers.mcp_readability_scoring``): the orchestrator calls :meth:`run` with the
per-endpoint context. Its binary summary metric is "within token budget".
"""

from collections.abc import Sequence
import json
from typing import Any

from scorers.mcp_readability_scoring import EndpointContext, ScoreContribution

# Rough chars-per-token heuristic for estimating a tool's token footprint.
_CHARS_PER_TOKEN = 4


class McpToolMetricsScorer:
    """Computes deterministic size/cost metrics for a list of MCP tools."""

    # Result-row columns this scorer contributes.
    COLUMNS = [
        "mcp_readability_total_tools",
        "mcp_readability_estimated_tokens",
        "mcp_readability_token_budget_used_percent",
    ]

    def __init__(self, config: dict | None = None, global_models=None):
        # Accept global_models for signature parity with the other scorers even
        # though this deterministic scorer needs no model.
        self.name = "mcp_tool_metrics"
        config = config or {}
        # Token budget from this scorer's own config block; a per-call value
        # passed to score() still wins.
        self.token_budget = config.get("token_budget")

    def run(self, context: EndpointContext) -> ScoreContribution:
        """Evaluate one endpoint: compute metrics, pass iff within budget.

        An endpoint may override the configured budget with its own
        ``token_budget`` field.
        """
        budget = context.endpoint.get("token_budget", self.token_budget)
        metrics = self.score(context.tools, budget)
        used = metrics["token_budget_used_percent"]
        # No positive budget configured -> nothing to exceed -> pass.
        within_budget = used is None or used <= 100.0
        return ScoreContribution(
            row_fields={
                "mcp_readability_total_tools": metrics["total_tools"],
                "mcp_readability_estimated_tokens": metrics["estimated_tokens"],
                "mcp_readability_token_budget_used_percent": used or 0.0,
            },
            score=100 if within_budget else 0,
            logs=(
                f"total_tools={metrics['total_tools']}, "
                f"estimated_tokens={metrics['estimated_tokens']}, "
                f"token_budget_used_percent={used}"
            ),
        )

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
