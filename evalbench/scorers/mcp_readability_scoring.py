"""Shared interface for the plug-and-play MCP readability scorers.

The orchestrator fetches each endpoint's tools once, then hands every configured
scorer the same :class:`EndpointContext` and merges each scorer's
:class:`ScoreContribution` into the result row. A new scorer (e.g. conformance
testing) plugs in by registering its class in the orchestrator's registry and
adding a block under ``scorers:`` in the run config -- no orchestrator changes
needed.

Each mcp_readability scorer implements:

  - ``name``: str, must match its key under ``scorers:`` in the run config
    (also used as the ``comparator`` on its summary score rows).
  - ``COLUMNS``: list[str], the result-row columns it contributes.
  - ``run(context) -> ScoreContribution``: evaluate one endpoint.

This module is deliberately kept free of any concrete-scorer imports so both the
scorers and the orchestrator can import it without a cycle.
"""

from dataclasses import dataclass, field
from typing import Any


@dataclass
class EndpointContext:
    """Everything a scorer needs to evaluate a single endpoint.

    Populated once by the orchestrator after fetching + rendering the tools, then
    shared across all configured scorers for that endpoint.
    """

    product_name: str
    endpoint: dict
    tools: list  # list[mcp.types.Tool]
    man_page: str
    exceptions: list  # applicable waivers for this endpoint


@dataclass
class ScoreContribution:
    """What a scorer returns for one endpoint.

    - ``row_fields``: result-row columns (keys must be within the scorer's
      declared ``COLUMNS``) merged into the endpoint's evals row.
    - ``score``: 0/100 binary pass for the shared analyzer summary (aggregated
      under the scorer's ``name`` as ``comparator``).
    - ``logs``: human-readable ``comparison_logs`` for the score row.
    """

    row_fields: dict[str, Any] = field(default_factory=dict)
    score: int = 100
    logs: str = ""


# Severity display order, and the badge each finding bullet is prefixed with now
# that findings are grouped by tool rather than by severity.
SEVERITY_BADGES = {"P0": "🚫 P0", "P1": "⚠️ P1", "P2": "💡 P2"}

# Bucket for findings that belong to the tool set rather than to any one tool --
# too many tools for one server, a missing polling or discovery tool, a parameter
# named inconsistently across tools. Listed ahead of the per-tool buckets.
GENERAL = "General issues"


def findings_by_tool(findings) -> list[tuple[str, list[dict]]]:
    """Group style-judge findings into one list per affected tool.

    Shared by the dashboard HTML (``McpStyleReadabilityScorer.to_html``) and the
    Markdown comparison report so both present the same buckets. The judge
    attributes each finding to one tool, but a finding naming several
    (comma-separated) is listed under each of them; findings with no tool land in
    :data:`GENERAL`.

    Buckets are ordered with :data:`GENERAL` first, then tools alphabetically;
    within a bucket findings run P0 -> P1 -> P2.
    """
    buckets: dict[str, list[dict]] = {}
    for finding in findings:
        if not isinstance(finding, dict):
            continue
        named = [t.strip() for t in str(finding.get("tool", "")).split(",")]
        for tool in [t for t in named if t] or [GENERAL]:
            buckets.setdefault(tool, []).append(finding)

    severity_rank = {sev: i for i, sev in enumerate(SEVERITY_BADGES)}
    ordered = sorted(buckets, key=lambda t: (t != GENERAL, t.lower()))
    return [
        (
            tool,
            sorted(
                buckets[tool],
                key=lambda f: severity_rank.get(
                    str(f.get("severity", "")).upper(), len(severity_rank)
                ),
            ),
        )
        for tool in ordered
    ]


def severity_tally(findings: list[dict]) -> str:
    """Summarize a tool's findings as e.g. ``"1 P0, 2 P2"`` (zeros omitted)."""
    counts = {sev: 0 for sev in SEVERITY_BADGES}
    other = 0
    for finding in findings:
        sev = str(finding.get("severity", "")).upper()
        if sev in counts:
            counts[sev] += 1
        else:
            other += 1
    parts = [f"{n} {sev}" for sev, n in counts.items() if n]
    if other:
        parts.append(f"{other} unclassified")
    return ", ".join(parts) or "no findings"
