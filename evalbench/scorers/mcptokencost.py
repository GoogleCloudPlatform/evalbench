"""Deterministic, approximate token-cost scorer for MCP compliance runs.

Reads the pre-computed token count out of the eval output emitted by the
ComplianceCheckEvaluator. No external deps, no API calls. Same input →
same output, always.

- Per-tool row: ``score = approx_tokens`` for that tool's YAML.
- Per-endpoint aggregate row: ``score = avg_approx_tokens`` across tools.
"""

from typing import Any, Tuple

from scorers import comparator


class McpTokenCost(comparator.Comparator):
    def __init__(self, config: dict):
        self.name = "mcp_token_cost"
        self.config = config or {}

    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
        generated_error: Any,
    ) -> Tuple[float, str]:
        eo = generated_eval_result
        if not isinstance(eo, dict):
            return 0.0, "no eval_output dict available"
        if eo.get("probe_error"):
            return 0.0, f"probe_error: {eo['probe_error']}"

        if eo.get("is_aggregate"):
            tokens = eo.get("avg_approx_tokens", 0)
            return (
                float(tokens),
                f"avg {tokens} tokens across {eo.get('tool_count', 0)} tools "
                f"(total {eo.get('total_approx_tokens', 0)})",
            )

        tokens = eo.get("approx_tokens", 0)
        return (
            float(tokens),
            f"{tokens} tokens (len(yaml)//4) for tool {eo.get('tool_name')!r}",
        )
