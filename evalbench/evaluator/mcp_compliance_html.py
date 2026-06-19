"""Render MCP compliance judge findings as light HTML.

Output shape matches sample_summary.txt — header, General Feedback grouped
by severity, then Detailed Feedback by Tool. No inline CSS, no JS. Uses
<details>/<summary> collapsibles per severity bucket so a long report
collapses cleanly in BQ column previews / Looker tooltips / Markdown
viewers that allow raw HTML.

Two entrypoints:
  render_endpoint_summary(...) → full report for an aggregate row
  render_tool_slice(...)        → just the {TOOL: ...} section for a row
"""

from __future__ import annotations

import html
from typing import Iterable


_SEVERITIES = ("P0", "P1", "P2")


def render_endpoint_summary(
    endpoint_id: str,
    tool_count: int,
    avg_tokens: int,
    total_tokens: int,
    severity_counts: dict[str, int],
    general_findings: dict[str, list[dict]],
    per_tool_findings: dict[str, dict[str, list[dict]]],
) -> str:
    parts: list[str] = []
    parts.append("<h2>MCP Compliance Review Feedback</h2>")
    parts.append(
        f"<p><b>Endpoint:</b> <code>{html.escape(endpoint_id)}</code> "
        f"&nbsp; <b>Tools:</b> {tool_count} "
        f"&nbsp; <b>Avg tokens:</b> {avg_tokens} "
        f"&nbsp; <b>Total tokens:</b> {total_tokens} "
        f"&nbsp; <b>Findings:</b> "
        f"P0={severity_counts.get('P0', 0)}, "
        f"P1={severity_counts.get('P1', 0)}, "
        f"P2={severity_counts.get('P2', 0)}</p>"
    )

    parts.append("<h3>General Feedback</h3>")
    parts.append(_render_severity_groups(general_findings))

    parts.append("<h3>Detailed Feedback by Tool</h3>")
    if not per_tool_findings:
        parts.append("<p><i>No tool-specific findings.</i></p>")
    else:
        for tool_name, findings in per_tool_findings.items():
            if not _has_any_finding(findings):
                continue
            parts.append(render_tool_slice(tool_name, findings))

    return "".join(parts)


def render_tool_slice(tool_name: str, findings: dict[str, list[dict]]) -> str:
    if not _has_any_finding(findings):
        return (
            f"<h4>{html.escape(tool_name)}</h4>"
            "<p><i>No findings.</i></p>"
        )
    return (
        f"<h4>{html.escape(tool_name)}</h4>"
        + _render_severity_groups(findings)
    )


def _render_severity_groups(findings: dict[str, list[dict]]) -> str:
    chunks: list[str] = []
    for sev in _SEVERITIES:
        items = findings.get(sev) or []
        if not items:
            continue
        # P0 and P1 open by default (they need eyes); P2 collapsed.
        open_attr = " open" if sev in ("P0", "P1") else ""
        chunks.append(
            f"<details{open_attr}><summary><b>{sev}</b> ({len(items)})</summary>"
            f"<ul>{_render_finding_list(items)}</ul></details>"
        )
    if not chunks:
        return "<p><i>No findings.</i></p>"
    return "".join(chunks)


def _render_finding_list(items: Iterable[dict]) -> str:
    out: list[str] = []
    for f in items:
        title = html.escape((f.get("title") or "").strip() or "<untitled>")
        desc = html.escape((f.get("description") or "").strip())
        if desc:
            out.append(f"<li><b>{title}.</b> {desc}</li>")
        else:
            out.append(f"<li><b>{title}.</b></li>")
    return "".join(out)


def _has_any_finding(findings: dict[str, list[dict]]) -> bool:
    return any(findings.get(sev) for sev in _SEVERITIES)
