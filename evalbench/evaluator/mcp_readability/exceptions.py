"""Loading and matching of per-endpoint style-rule exceptions (waivers).

An exceptions file lets an operator waive a specific style requirement for a
specific endpoint when it legitimately cannot comply. Waived rules are passed to
the scorer so they are excluded from the P0/P1/P2 counts and surfaced separately
in the feedback.

File schema (see ``datasets/mcp_readability/exceptions.yaml``)::

    exceptions:
      - product_name: "Cloud SQL"     # any of product_name / endpoint_type
        rule_id: "tool-names"
        reason: "..."
      - endpoint_type: AUTOPUSH       # "*" or omitted field = match-all
        rule_id: "use-enums"
        reason: "..."
"""

import logging
from util.config import load_yaml_config


def load_exceptions(path: str) -> list[dict]:
    """Load the exceptions list from a YAML file. Missing/empty -> []."""
    if not path:
        return []
    parsed = load_yaml_config(path)
    if not parsed:
        return []
    exceptions = parsed.get("exceptions") or []
    if not isinstance(exceptions, list):
        logging.warning(
            "mcp_readability: 'exceptions' in %s is not a list; ignoring.", path
        )
        return []
    return exceptions


def _matches(field_value, exception_value) -> bool:
    """A matcher field matches when it is absent, '*', or equal (case-insensitive)."""
    if exception_value is None or exception_value == "*":
        return True
    if field_value is None:
        return False
    return str(field_value).strip().lower() == str(exception_value).strip().lower()


def applicable_exceptions(endpoint: dict, all_exceptions: list[dict]) -> list[dict]:
    """Return exceptions whose matchers all apply to ``endpoint``.

    Matchers considered: ``product_name`` and ``endpoint_type`` (the endpoint's
    identity in #469's ``endpoints.yaml``). Each exception keeps its ``rule_id``
    and ``reason`` for the scorer prompt.
    """
    matched = []
    for exc in all_exceptions:
        if not isinstance(exc, dict):
            continue
        if not exc.get("rule_id"):
            continue
        if (
            _matches(endpoint.get("product_name"), exc.get("product_name"))
            and _matches(endpoint.get("endpoint_type"), exc.get("endpoint_type"))
        ):
            matched.append(
                {
                    "rule_id": exc.get("rule_id"),
                    "reason": exc.get("reason", ""),
                }
            )
    return matched
