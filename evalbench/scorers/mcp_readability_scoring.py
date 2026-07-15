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
