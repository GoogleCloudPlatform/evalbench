"""Format the graded report as a readable block for the run log.

The report is otherwise only reachable as a JSON string in ``scores.csv``, so
without this the synthesis pass's prose is generated and never seen. Renders
whatever is present, so a run with synthesis disabled still prints the
deterministic scores, gaps, and distributions.
"""


def _distribution_lines(report: dict) -> list[str]:
    # Sub-scorers contribute dataset-wide breakdowns that the scorer hoists to
    # the report's top level, where they are the only dict-valued entries.
    lines = []
    for key, value in report.items():
        if isinstance(value, dict) and value:
            counts = ", ".join(f"{k}={v}" for k, v in value.items())
            lines.append(f"{key}: {counts}")
    return lines


def _bullets(label: str, items) -> list[str]:
    if not items:
        return []
    return [f"  {label}:"] + [f"    - {item}" for item in items]


def render_report(report: dict) -> str:
    """Return the report as an indented multi-line block."""
    lines = [
        f"=== Dataset Quality: {report.get('product_name')} ===",
        f"Score: {report.get('dataset_quality_score')} "
        f"({report.get('letter_grade')}) | {report.get('total_cujs')} CUJs",
    ]
    lines += _distribution_lines(report)
    if report.get("overall_summary"):
        lines += ["", f"Summary: {report['overall_summary']}"]

    for category in report.get("categories") or []:
        lines += ["", f"--- {category.get('name')}: {category.get('score')} ---"]
        for name, score in (category.get("sub_scores") or {}).items():
            lines.append(f"  {name}: {score}")
        if category.get("assessment"):
            lines.append(f"  Assessment: {category['assessment']}")
        lines += _bullets("Gaps", category.get("gaps"))
        lines += _bullets("Recommendations", category.get("recommendations"))

    actions = report.get("prioritized_actions") or []
    if actions:
        lines += ["", "Prioritized actions:"]
        for action in actions:
            lines.append(
                f"  {action.get('priority')}. [{action.get('area')}] "
                f"{action.get('action')}"
            )
            if action.get("rationale"):
                lines.append(f"     Why: {action['rationale']}")
    return "\n".join(lines)
