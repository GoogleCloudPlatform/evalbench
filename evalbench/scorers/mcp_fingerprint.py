"""Fingerprints over every input that can change MCP readability feedback.

The readability judge is a thinking model: even at temperature 0 an unchanged
tool surface can yield different findings from one run to the next, so counts
move with no stated reason. The model cannot be made repeatable, so its inputs
are hashed instead and unchanged ones are not re-judged.

A tool is hashed by its rendered man-page section rather than its Tool fields,
which makes "the fingerprints match" and "the judge would read identical text"
the same statement. Hashing fields would need a list of which fields matter,
kept in sync with the renderer by hand. Everything else feeding the prompt --
style guide, prompt text, model, product name, waivers -- is hashed separately
as the judge fingerprint.

No concrete-scorer imports here, so both the scorers and the orchestrator can
import this without a cycle.
"""

from collections.abc import Sequence
import hashlib
import json
from typing import Any

from generators.models.mcp_tool_formatter import format_tool_section


def sha256_text(text: str) -> str:
    """Return the sha256 hex digest of text, UTF-8 encoded."""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def tool_fingerprints(tools: Sequence[Any]) -> dict[str, str]:
    """Map each tool name to the sha256 of its rendered man-page section.

    A later duplicate of a tool name is ignored, matching the first-declaration-
    wins merge the tools generator applies across sources.
    """
    fingerprints: dict[str, str] = {}
    for tool in tools or []:
        name = getattr(tool, "name", "") or ""
        if not name or name in fingerprints:
            continue
        fingerprints[name] = sha256_text(format_tool_section(tool))
    return fingerprints


def toolset_fingerprint(fingerprints: dict[str, str]) -> str:
    """Return a single hash over the whole tool surface.

    Computed over (name, fingerprint) pairs sorted by name, so reordering the
    tools does not change it. Order is excluded because the per-tool
    fingerprints already carry the guarantee, and making it significant would
    force a global re-judge whenever a source is re-declared.
    """
    payload = sorted((name, fp) for name, fp in (fingerprints or {}).items())
    return sha256_text(json.dumps(payload, separators=(",", ":")))


def canonical_exceptions(exceptions: list | None) -> list[dict]:
    """Reduce waivers to their prompt-visible fields, sorted for stability.

    Only rule_id and reason reach the prompt, and the orchestrator's match order
    follows the exceptions file's layout rather than anything semantic, so
    reordering the file must not count as a judge change.
    """
    canonical = []
    for exc in exceptions or []:
        if not isinstance(exc, dict):
            continue
        canonical.append(
            {
                "rule_id": str(exc.get("rule_id", "")),
                "reason": str(exc.get("reason", "")),
            }
        )
    return sorted(canonical, key=lambda e: (e["rule_id"], e["reason"]))


def judge_fingerprint(components: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Hash every judge input other than the tools themselves.

    Components are the style guide sha and path, prompt version and sha, model,
    scorer name, product name, and canonicalised waivers. They are returned
    alongside the hash because a mismatch has to be explained to a human:
    diffing them turns an unexplained count change into "the style guide
    changed".
    """
    canonical = json.dumps(components, sort_keys=True, separators=(",", ":"),
                           default=str)
    return sha256_text(canonical), dict(components)


def component_diff(previous: dict | None, current: dict | None) -> list[str]:
    """Return the names of the judge components that differ between two runs."""
    previous = previous or {}
    current = current or {}
    return sorted(
        key
        for key in set(previous) | set(current)
        if previous.get(key) != current.get(key)
    )
