"""Dataset Quality tab: latest grade per product, and the full report per run.

Reads the table view from results/dataset_quality_cache.json (see
precompute_dataset_quality). The detail view re-reads the single run's scores.csv,
since the gaps, evidence and recommendations behind a grade are far too bulky to
keep in a cache that every page render loads.
"""

import csv
import json
import logging
import os

import mesop as me

from precompute_dataset_quality import CACHE_FILENAME, SUMMARY_COMPARATOR

csv.field_size_limit(10**9)

# Display order for the score columns; anything unrecognised is appended, so a new
# sub-scorer category still shows up without a viewer change.
CATEGORY_LABELS = {
    "tool_activation_faithfulness": "Tool Activation",
    "discoverability_coverage": "Discoverability",
    "error_recovery_coverage": "Error Recovery",
    "composition_coverage": "Composition",
    "cuj_diversity": "CUJ Diversity",
}

_UNGRADED = "#64748b"
_BORDER = me.Border.all(me.BorderSide(width="1px", color="#e2e8f0", style="solid"))
_CELL_PADDING = me.Padding.symmetric(vertical="10px", horizontal="16px")

_CARD_BASE = dict(
    background="#ffffff",
    border_radius="10px",
    border=me.Border.all(me.BorderSide(width="1px", color="#e5e7eb", style="solid")),
    padding=me.Padding.all("16px"),
    box_shadow="0 1px 3px rgba(0,0,0,0.06)",
)


def _card_style(**overrides):
    return me.Style(**{**_CARD_BASE, **overrides})


def get_results_dir():
    # Try to read from environment variable
    res_dir = os.environ.get("RESULTS_DIR")
    if res_dir:
        return res_dir

    # Check multiple locations for results directory
    results_dir_candidates = [
        "/tmp_session_files/results",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "results"),
        os.path.join(os.getcwd(), "results"),
    ]

    for candidate in results_dir_candidates:
        if os.path.exists(candidate) and os.path.isdir(candidate):
            return candidate

    return results_dir_candidates[1]  # Fallback to default


def score_color(score):
    """Colour a 0-100 score on the same bands the letter grade uses."""
    if score is None:
        return _UNGRADED
    if score >= 75:
        return "#16a34a"
    if score >= 60:
        return "#ca8a04"
    return "#dc2626"


def grade_color(letter):
    if letter in ("A", "B"):
        return "#16a34a"
    if letter == "C":
        return "#ca8a04"
    if letter in ("D", "F"):
        return "#dc2626"
    return _UNGRADED


def category_label(name):
    return CATEGORY_LABELS.get(name, name.replace("_", " ").title())


def _fmt_score(score):
    return "—" if score is None else f"{score:.1f}"


def _fmt_time(run_time):
    # run_time carries microseconds; seconds are already more precision than a
    # weekly job warrants.
    return (run_time or "")[:16]


def load_cache(results_dir):
    cache_file = os.path.join(results_dir, CACHE_FILENAME)
    if not os.path.exists(cache_file):
        return []
    try:
        with open(cache_file) as f:
            return json.load(f)
    except Exception as e:
        logging.warning("Could not read dataset quality cache: %s", e)
        return []


def load_latest_by_product(results_dir):
    """Latest run per product, worst score first so the actionable rows lead."""
    latest = {}
    for entry in load_cache(results_dir):
        product = entry.get("product_name")
        if not product:
            continue
        current = latest.get(product)
        if current is None or entry.get("run_time", "") > current.get("run_time", ""):
            latest[product] = entry
    # Ungraded runs sort last: they need a rerun, not dataset work.
    return sorted(
        latest.values(),
        key=lambda e: (e.get("score") is None, e.get("score") or 0),
    )


def ordered_categories(entries):
    seen = [c for c in CATEGORY_LABELS if any(c in e["category_scores"] for e in entries)]
    extra = sorted(
        {c for e in entries for c in e["category_scores"]} - set(CATEGORY_LABELS)
    )
    return seen + extra


def load_report(results_dir, job_id):
    """Reassemble the full report from a run's scores.csv, or None if not a DQ run."""
    scores_file = os.path.join(results_dir, job_id, "scores.csv")
    if not os.path.exists(scores_file):
        return None

    report = None
    categories = []
    try:
        with open(scores_file, newline="") as f:
            for row in csv.DictReader(f):
                comparator = row.get("comparator", "")
                try:
                    payload = json.loads(row.get("comparison_logs") or "{}")
                except json.JSONDecodeError:
                    continue
                if comparator == SUMMARY_COMPARATOR:
                    report = payload
                elif "sub_scores" in payload:
                    categories.append(payload)
    except Exception as e:
        logging.warning("Could not read scores.csv for %s: %s", job_id, e)
        return None

    if report is None:
        return None
    report["categories"] = categories
    return report


def _stat_card(label, value, color="#0f172a", detail=""):
    with me.box(style=_card_style()):
        me.text(
            label,
            style=me.Style(
                font_size="12px",
                font_weight="600",
                color="#64748b",
                text_transform="uppercase",
                letter_spacing="0.05em",
                margin=me.Margin(bottom="4px"),
            ),
        )
        me.text(value, style=me.Style(font_size="24px", font_weight="700", color=color))
        if detail:
            me.text(
                detail,
                style=me.Style(font_size="12px", color="#64748b", margin=me.Margin(top="4px")),
            )


def _fleet_summary(entries):
    graded = [e for e in entries if e.get("score") is not None]

    distribution = {}
    for entry in graded:
        letter = entry.get("letter_grade")
        distribution[letter] = distribution.get(letter, 0) + 1
    grades = " ".join(
        f"{letter}:{distribution[letter]}"
        for letter in ("A", "B", "C", "D", "F")
        if letter in distribution
    )

    total_cujs = sum(e.get("total_cujs") or 0 for e in entries)

    # Unweighted mean over products, so a product with a large dataset doesn't
    # drown out a small one — every product's dataset is owned by its own team.
    average = sum(e["score"] for e in graded) / len(graded) if graded else None

    with me.box(
        style=me.Style(
            display="grid",
            grid_template_columns="repeat(3, 1fr)",
            gap="16px",
            margin=me.Margin(bottom="20px"),
        )
    ):
        _stat_card(
            "Products graded",
            str(len(graded)),
            detail=grades or "no grades yet",
        )
        _stat_card(
            "Average score",
            _fmt_score(average),
            color=score_color(average),
            detail=f"across {len(graded)} products" if graded else "",
        )
        _stat_card(
            "Total CUJs",
            str(total_cujs),
            detail=f"across {len(entries)} datasets",
        )


def _header_cell(label, width):
    with me.box(
        style=me.Style(
            display="table-cell",
            padding=_CELL_PADDING,
            text_align="center",
            border=_BORDER,
            width=width,
            white_space="nowrap",
        )
    ):
        me.text(label)


def _cell(width="auto", align="center"):
    return me.Style(
        display="table-cell",
        padding=_CELL_PADDING,
        text_align=align,
        border=_BORDER,
        width=width,
        vertical_align="middle",
    )


def dataset_quality_component():
    results_dir = get_results_dir()
    entries = load_latest_by_product(results_dir)

    me.text(
        "Dataset Quality",
        style=me.Style(font_size="22px", font_weight="700", margin=me.Margin(bottom="4px")),
    )
    me.text(
        "Latest grade for each product's CUJ dataset. Click a product to open its "
        "full report.",
        style=me.Style(color="#64748b", margin=me.Margin(bottom="20px")),
    )

    if not entries:
        me.text(
            "No dataset quality runs found yet. They appear here once the weekly "
            "grading job has run and the precompute has picked it up.",
            style=me.Style(color="#64748b"),
        )
        return

    categories = ordered_categories(entries)
    _fleet_summary(entries)

    with me.box(style=me.Style(overflow_x="auto", width="100%")):
        with me.box(
            style=me.Style(
                display="table",
                width="100%",
                border=me.Border.all(
                    me.BorderSide(width="1px", color="#e5e7eb", style="solid")
                ),
                border_radius="8px",
                background="#ffffff",
            )
        ):
            with me.box(
                style=me.Style(
                    display="table-row",
                    background="#f8fafc",
                    font_weight="bold",
                    color="#475569",
                    font_size="12px",
                    text_transform="uppercase",
                    letter_spacing="0.05em",
                )
            ):
                _header_cell("Product", "20ch")
                _header_cell("CUJs", "8ch")
                _header_cell("Score", "8ch")
                _header_cell("Grade", "8ch")
                for category in categories:
                    _header_cell(category_label(category), "14ch")
                _header_cell("Last run", "18ch")

            for entry in entries:
                _product_row(entry, categories)


def _product_row(entry, categories):
    with me.box(style=me.Style(display="table-row", background="#ffffff")):
        with me.box(style=_cell("20ch", align="left")):
            me.markdown(
                f'<a href="/?job_id={entry["job_id"]}" class="pill-link">'
                f'{entry["product_name"]}</a>'
            )

        with me.box(style=_cell("8ch")):
            me.text(str(entry.get("total_cujs") or "—"), style=me.Style(color="#334155"))

        score = entry.get("score")
        with me.box(style=_cell("8ch")):
            me.text(_fmt_score(score), style=me.Style(color=score_color(score)))

        with me.box(style=_cell("8ch")):
            _grade_badge(entry.get("letter_grade"))

        for category in categories:
            value = entry["category_scores"].get(category)
            with me.box(style=_cell("14ch")):
                me.text(_fmt_score(value), style=me.Style(color=score_color(value)))

        with me.box(style=_cell("18ch")):
            me.text(
                _fmt_time(entry.get("run_time")),
                style=me.Style(color="#334155", font_family="monospace"),
            )


def _grade_badge(letter):
    with me.box(
        style=me.Style(
            display="inline-block",
            background=grade_color(letter),
            color="#ffffff",
            font_weight="700",
            border_radius="6px",
            padding=me.Padding.symmetric(vertical="2px", horizontal="10px"),
        )
    ):
        me.text(letter or "—")


def _bullets(title, items):
    if not items:
        return
    me.text(
        title,
        style=me.Style(
            font_size="12px",
            font_weight="600",
            color="#64748b",
            text_transform="uppercase",
            margin=me.Margin(top="12px", bottom="4px"),
        ),
    )
    for item in items:
        me.text(f"• {item}", style=me.Style(color="#334155", margin=me.Margin(bottom="4px")))


def dataset_quality_detail_component(results_dir, report, job_id):
    """Render a graded report. ``report`` comes from :func:`load_report`."""
    score = report.get("dataset_quality_score")

    with me.box(
        style=me.Style(
            display="flex",
            align_items="center",
            gap="24px",
            flex_wrap="wrap",
            margin=me.Margin(bottom="16px"),
        )
    ):
        me.text(
            str(report.get("product_name") or "Dataset Quality"),
            style=me.Style(font_size="26px", font_weight="700"),
        )
        me.text(
            _fmt_score(score),
            style=me.Style(font_size="26px", font_weight="700", color=score_color(score)),
        )
        _grade_badge(report.get("letter_grade"))
        me.text(
            f"{report.get('total_cujs', 0)} CUJs",
            style=me.Style(color="#64748b", font_weight="500"),
        )
        me.text(
            job_id,
            style=me.Style(color="#94a3b8", font_family="monospace", font_size="12px"),
        )

    if report.get("error"):
        with me.box(
            style=me.Style(
                background="#fef2f2",
                border=me.Border.all(me.BorderSide(width="1px", color="#fecaca")),
                border_radius="8px",
                padding=me.Padding.all("16px"),
                margin=me.Margin(bottom="16px"),
            )
        ):
            me.text(
                f"This run was not graded: {report['error']}",
                style=me.Style(color="#b91c1c", font_weight="500"),
            )

    if report.get("overall_summary"):
        with me.box(style=_card_style(margin=me.Margin(bottom="16px"))):
            me.text("Summary", type="headline-6")
            me.text(report["overall_summary"], style=me.Style(color="#334155"))

    distribution = report.get("cuj_path_distribution") or {}
    if distribution:
        with me.box(style=_card_style(margin=me.Margin(bottom="16px"))):
            me.text("CUJ path distribution", type="headline-6")
            with me.box(
                style=me.Style(display="flex", gap="24px", flex_wrap="wrap",
                               margin=me.Margin(top="8px"))
            ):
                for path, count in distribution.items():
                    with me.box(style=me.Style(display="flex", flex_direction="column")):
                        me.text(
                            str(count),
                            style=me.Style(
                                font_size="20px",
                                font_weight="700",
                                # A path with no CUJs at all is the gap worth seeing.
                                color="#dc2626" if not count else "#0f172a",
                            ),
                        )
                        me.text(path, style=me.Style(font_size="12px", color="#64748b"))

    actions = sorted(
        report.get("prioritized_actions") or [], key=lambda a: a.get("priority", 0)
    )
    if actions:
        with me.box(style=_card_style(margin=me.Margin(bottom="16px"))):
            me.text("Prioritized actions", type="headline-6")
            for action in actions:
                with me.box(style=me.Style(margin=me.Margin(top="12px"))):
                    me.text(
                        f"{action.get('priority')}. [{action.get('area')}] "
                        f"{action.get('action')}",
                        style=me.Style(font_weight="600", color="#0f172a"),
                    )
                    if action.get("rationale"):
                        me.text(
                            action["rationale"],
                            style=me.Style(
                                color="#64748b",
                                font_size="13px",
                                margin=me.Margin(top="2px"),
                            ),
                        )

    me.text("Categories", type="headline-6")
    for category in sorted(
        report.get("categories") or [], key=lambda c: c.get("score") or 0
    ):
        _category_card(category)


def _category_card(category):
    score = category.get("score")
    with me.box(style=_card_style(margin=me.Margin(top="12px"))):
        with me.box(
            style=me.Style(display="flex", align_items="center", gap="12px")
        ):
            me.text(
                category_label(category.get("name", "")),
                style=me.Style(font_size="16px", font_weight="700"),
            )
            me.text(
                _fmt_score(score),
                style=me.Style(font_weight="700", color=score_color(score)),
            )

        parts = [
            f"{name}: {_fmt_score(value)}"
            for name, value in (category.get("sub_scores") or {}).items()
        ]
        parts += [f"{k}: {v}" for k, v in (category.get("metrics") or {}).items()]
        if parts:
            me.text(
                "  ·  ".join(parts),
                style=me.Style(
                    color="#64748b",
                    font_size="13px",
                    font_family="monospace",
                    margin=me.Margin(top="6px"),
                ),
            )

        if category.get("assessment"):
            me.text(
                category["assessment"],
                style=me.Style(color="#334155", margin=me.Margin(top="10px")),
            )

        _bullets("Gaps", category.get("gaps"))
        _bullets("Recommendations", category.get("recommendations"))
