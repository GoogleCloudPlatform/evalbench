"""Registered mcp_readability scorers, keyed by the name used under ``scorers:``
in the run config (also each scorer's ``comparator`` in the summary).
"""

from scorers.mcp_readability.style_readability import McpStyleReadabilityScorer
from scorers.mcp_readability.tool_metrics import McpToolMetricsScorer

SCORER_REGISTRY = {
    "mcp_tool_metrics": McpToolMetricsScorer,
    "mcp_style_readability": McpStyleReadabilityScorer,
}
