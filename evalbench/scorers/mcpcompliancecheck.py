"""Thin extractor scorer for MCP compliance findings.

Reads the pre-computed findings out of the eval output (the evaluator
makes the LLM judge call exactly once per endpoint; see design doc
§7.1.1). This scorer just converts the per-row severity counts into a
numeric score and lifts the pre-rendered HTML into ``comparison_logs``.

Score formula (lower is better, lower-bounded at 0):
    P0 * 10 + P1 * 3 + P2 * 1
"""

from typing import Any, Tuple

from scorers import comparator


_DEFAULT_WEIGHTS = {"P0": 10, "P1": 3, "P2": 1}


class McpComplianceCheck(comparator.Comparator):
    def __init__(self, config: dict):
        self.name = "mcp_compliance_check"
        self.config = config or {}
        weights = self.config.get("severity_weights") or {}
        self.weights = {**_DEFAULT_WEIGHTS, **weights}

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

        counts = eo.get("severity_counts") or {"P0": 0, "P1": 0, "P2": 0}
        score = sum(self.weights.get(sev, 0) * n for sev, n in counts.items())

        if eo.get("is_aggregate"):
            html = eo.get("html_summary") or _summary_placeholder(counts)
        else:
            html = eo.get("html_slice") or _tool_placeholder(eo.get("tool_name"), counts)

        return float(score), html


def _summary_placeholder(counts: dict) -> str:
    return (
        "<p><i>Compliance summary not yet rendered "
        f"(P0={counts.get('P0', 0)}, P1={counts.get('P1', 0)}, "
        f"P2={counts.get('P2', 0)}).</i></p>"
    )


def _tool_placeholder(tool_name, counts: dict) -> str:
    return (
        f"<p><i>No detailed findings for tool {tool_name!r} "
        f"(P0={counts.get('P0', 0)}, P1={counts.get('P1', 0)}, "
        f"P2={counts.get('P2', 0)}).</i></p>"
    )
