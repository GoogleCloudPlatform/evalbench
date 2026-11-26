"""Stats across trials for the Dashboard tab.

An agent run with num_trials above 1 writes run_stats.csv, with one row for
each scenario, and adds the stats of the suite to summary.csv. A run without
run_stats.csv gets no output from this module.
"""

import logging
import math
import os
from typing import Any, Optional

import mesop as me
import pandas as pd

RUN_STATS_FILE = "run_stats.csv"
# The scorer that decides if a trial passes.
PASS_METRIC = "goal_completion"
# A file without these columns is not a stats file.
_REQUIRED_COLUMNS = {"scenario_id", "num_trials", "scored_trials", "status"}
# The stats of the suite, in the row of the pass metric of summary.csv.
_SUITE_COLUMNS = (
    "pass_rate", "interval_low", "interval_high", "flakiness", "confidence")

_GREEN = "#10b981"
_AMBER = "#f59e0b"
_RED = "#ef4444"
_GREY = "#9ca3af"
_TEXT = "#0f172a"
_MUTED = "#64748b"
_LINE = "#e5e7eb"
_STATUS_COLORS = {"PASS": _GREEN, "FAIL": _RED, "ERROR": _GREY}
# The flaky scenarios come first, then these statuses in this order.
_STATUS_ORDER = {"FAIL": 1, "ERROR": 2, "PASS": 3}
_EMPTY = "–"

_HEADERS = ("Scenario", "Status", "Trials passed", "Pass rate",
            "95% interval", "Flakiness", "Confidence")
_GRID = "minmax(180px, 2fr) 80px minmax(190px, 1.5fr) 80px 120px 80px 90px"
_NOTES = (
    "Pass rate: passed trials divided by scored trials. An unscored trial "
    "has an agent error or a scorer error. It does not count as a pass or "
    "as a failure.",
    "95% interval: the Wilson interval. It is the range that holds the true "
    "pass rate with 95% certainty.",
    "Flakiness: 4p(1 - p) for the pass rate p. It is 0 if all scored trials "
    "agree and 1 at 50/50.",
    "Confidence: the chance, from 0 to 1, that the true pass rate is above "
    "90%.",
)


def _number(value: Any) -> Optional[float]:
    """Returns `value` as a float, or None if it is empty or not a number."""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


def _count(value: Any) -> int:
    number = _number(value)
    return max(int(number), 0) if number is not None else 0


def format_rate(value: Any) -> str:
    """Formats a value from 0 to 1 as a percentage."""
    number = _number(value)
    return _EMPTY if number is None else f"{number * 100:.0f}%"


def format_interval(low: Any, high: Any) -> str:
    if _number(low) is None or _number(high) is None:
        return _EMPTY
    return f"{format_rate(low)} to {format_rate(high)}"


def format_score(value: Any) -> str:
    """Formats a flakiness or a confidence value with 2 decimals."""
    number = _number(value)
    return _EMPTY if number is None else f"{number:.2f}"


def _rate_color(rate: Optional[float]) -> str:
    """Returns the color of a pass rate from 0 to 1.

    The limits are the same as for the metric cards of the dashboard: green
    from 80%, red below 40%, and amber between them.
    """
    if rate is None:
        return _TEXT
    if rate >= 0.8:
        return _GREEN
    return _RED if rate < 0.4 else _AMBER


def load_run_stats(results_dir: str) -> Optional[pd.DataFrame]:
    """Returns the rows of run_stats.csv, or None if the run has no stats."""
    path = os.path.join(results_dir, RUN_STATS_FILE)
    if not os.path.exists(path):
        return None
    try:
        run_stats = pd.read_csv(path)
    except Exception as e:
        logging.warning("Could not read %s: %s", path, e)
        return None
    if run_stats.empty or not _REQUIRED_COLUMNS <= set(run_stats.columns):
        return None
    return run_stats


def _trials_text(passed: Optional[int], scored: int, unscored: int) -> str:
    parts = []
    if passed is not None:
        parts.append(f"{passed}/{scored}")
    if unscored:
        parts.append(f"{unscored} unscored")
    return ", ".join(parts) or _EMPTY


def scenario_rows(run_stats: pd.DataFrame) -> list[dict]:
    """Returns the values to show for each scenario, flaky scenarios first.

    The pass stats of a scenario are empty if it has no scored trial or if
    the job has no pass metric.
    """
    rows = []
    for record in run_stats.to_dict(orient="records"):
        scored = _count(record.get("scored_trials"))
        unscored = _count(record.get("unscored_trials"))
        passed = None
        if _number(record.get("passed_trials")) is not None:
            passed = min(_count(record.get("passed_trials")), scored)
        status = record.get("status")
        rows.append({
            "scenario_id": str(record.get("scenario_id")),
            "status": (status.strip().upper()
                       if isinstance(status, str) and status.strip()
                       else _EMPTY),
            "flaky": str(record.get("flaky")).strip().lower() == "true",
            "passed": passed,
            "failed": scored - passed if passed is not None else None,
            "unscored": unscored,
            "trials": _trials_text(passed, scored, unscored),
            "pass_rate": format_rate(record.get("pass_rate")),
            "interval": format_interval(
                record.get("interval_low"), record.get("interval_high")),
            "flakiness": format_score(record.get("flakiness")),
            "flakiness_value": _number(record.get("flakiness")) or 0.0,
            "confidence": format_score(record.get("confidence")),
        })
    rows.sort(key=lambda row: (
        0 if row["flaky"] else _STATUS_ORDER.get(row["status"], 4),
        -row["flakiness_value"]))
    return rows


def suite_stats(summary_df: Optional[pd.DataFrame]) -> dict[str, float]:
    """Returns the stats of the suite from the pass metric row of the summary.

    The result has no entry for a stat that the summary does not have.
    """
    if summary_df is None or "metric_name" not in summary_df.columns:
        return {}
    rows = summary_df[summary_df["metric_name"] == PASS_METRIC]
    if rows.empty:
        return {}
    stats = {}
    for column in _SUITE_COLUMNS:
        number = _number(rows.iloc[0].get(column))
        if number is not None:
            stats[column] = number
    return stats


def trials_summary(run_stats: pd.DataFrame) -> str:
    """Returns the number of scenarios and the number of trials of each."""
    counts = [_count(value) for value in run_stats["num_trials"]]
    low, high = min(counts), max(counts)
    trials = str(high) if low == high else f"{low} to {high}"
    scenarios = "scenario" if len(counts) == 1 else "scenarios"
    return f"{len(counts)} {scenarios}, {trials} trials each"


def suite_cards(suite: dict[str, float], rows: list[dict]) -> list[tuple]:
    """Returns the label, value, note, and color of each suite card."""
    cards = []
    if "pass_rate" in suite:
        interval = format_interval(
            suite.get("interval_low"), suite.get("interval_high"))
        cards.append((
            "Pass rate", format_rate(suite["pass_rate"]),
            f"95% interval: {interval}" if interval != _EMPTY else "",
            _rate_color(suite["pass_rate"])))
    flaky = sum(row["flaky"] for row in rows)
    cards.append((
        "Flaky scenarios", f"{flaky} of {len(rows)}",
        "Some trials passed and some failed",
        _AMBER if flaky else _GREEN))
    if "flakiness" in suite:
        cards.append((
            "Flakiness", format_score(suite["flakiness"]),
            "Average of the scenarios, from 0 to 1", _TEXT))
    if "confidence" in suite:
        cards.append((
            "Confidence", format_score(suite["confidence"]),
            "Average chance of a true pass rate above 90%", _TEXT))
    return cards


def _line() -> me.BorderSide:
    return me.BorderSide(width="1px", color=_LINE, style="solid")


def _card(label: str, value: str, note: str, color: str) -> None:
    with me.box(
        style=me.Style(
            flex_grow=1,
            flex_basis="180px",
            background="#ffffff",
            border_radius="10px",
            border=me.Border.all(_line()),
            padding=me.Padding.all("12px"),
            box_shadow="0 1px 3px rgba(0,0,0,0.06)",
        )
    ):
        me.text(
            label,
            style=me.Style(
                font_weight="600",
                font_size="12px",
                color="#374151",
                margin=me.Margin(bottom="4px"),
            ),
        )
        me.text(
            value,
            style=me.Style(font_weight="700", font_size="22px", color=color),
        )
        if note:
            me.text(
                note,
                style=me.Style(
                    font_size="12px",
                    color=_MUTED,
                    margin=me.Margin(top="4px"),
                ),
            )


def _row_style(header: bool = False) -> me.Style:
    return me.Style(
        display="grid",
        grid_template_columns=_GRID,
        align_items="center",
        gap="12px",
        min_width="900px",
        padding=me.Padding.symmetric(vertical="10px", horizontal="16px"),
        border=me.Border(bottom=_line()),
        background="#f8fafc" if header else "#ffffff",
    )


def _trials_cell(row: dict) -> None:
    """Draws the trials of a scenario as a bar and as text.

    The bar has one part for the passed trials, one for the failed trials,
    and one for the unscored trials. A scenario without pass stats and
    without unscored trials gets no bar.
    """
    segments = [(row["passed"], _GREEN), (row["failed"], _RED),
                (row["unscored"], _GREY)]
    with me.box(
        style=me.Style(display="flex", align_items="center", gap="10px")
    ):
        if row["passed"] is not None or row["unscored"]:
            with me.box(
                style=me.Style(
                    display="flex",
                    width="80px",
                    height="8px",
                    flex_shrink=0,
                    border_radius="4px",
                    overflow="hidden",
                    background=_LINE,
                )
            ):
                for count, color in segments:
                    if count:
                        with me.box(
                            style=me.Style(flex_grow=count, background=color)
                        ):
                            pass
        me.text(row["trials"], style=me.Style(font_size="13px", color=_TEXT))


def _table(rows: list[dict]) -> None:
    cell = me.Style(font_size="13px", color=_TEXT)
    with me.box(
        style=me.Style(
            margin=me.Margin(top="16px"),
            background="#ffffff",
            border_radius="10px",
            border=me.Border.all(_line()),
            box_shadow="0 1px 3px rgba(0,0,0,0.06)",
            overflow_x="auto",
        )
    ):
        with me.box(style=_row_style(header=True)):
            for header in _HEADERS:
                me.text(
                    header,
                    style=me.Style(
                        font_size="12px", font_weight="600", color=_MUTED),
                )
        for row in rows:
            with me.box(style=_row_style()):
                # me.text escapes the scenario id, which comes from a file.
                me.text(
                    row["scenario_id"],
                    style=me.Style(
                        font_size="13px",
                        font_weight="500",
                        color=_TEXT,
                        overflow_wrap="anywhere",
                    ),
                )
                with me.box(style=me.Style(display="flex")):
                    me.text(
                        row["status"],
                        style=me.Style(
                            font_size="11px",
                            font_weight="700",
                            color="#ffffff",
                            background=_STATUS_COLORS.get(
                                row["status"], _GREY),
                            border_radius="999px",
                            padding=me.Padding.symmetric(
                                vertical="2px", horizontal="8px"),
                        ),
                    )
                _trials_cell(row)
                me.text(row["pass_rate"], style=cell)
                me.text(row["interval"], style=cell)
                me.text(
                    row["flakiness"],
                    style=me.Style(
                        font_size="13px",
                        font_weight="700" if row["flaky"] else "400",
                        color=_AMBER if row["flaky"] else _TEXT,
                    ),
                )
                me.text(row["confidence"], style=cell)


def run_stats_component(
    results_dir: str, summary_df: Optional[pd.DataFrame] = None
) -> None:
    """Draws the stats across trials of a run.

    Draws nothing for a run without run_stats.csv, which is a run with one
    trial or a run that is not an agent run.
    """
    run_stats = load_run_stats(results_dir)
    if run_stats is None:
        return
    rows = scenario_rows(run_stats)

    me.text(
        "Stats across trials",
        style=me.Style(
            font_size="20px",
            font_weight="bold",
            margin=me.Margin(top="28px", bottom="4px"),
        ),
    )
    me.text(
        trials_summary(run_stats),
        style=me.Style(
            font_size="13px", color=_MUTED, margin=me.Margin(bottom="16px")),
    )
    with me.box(
        style=me.Style(
            display="flex",
            flex_direction="row",
            flex_wrap="wrap",
            gap="16px",
            width="100%",
        )
    ):
        for card in suite_cards(suite_stats(summary_df), rows):
            _card(*card)
    _table(rows)
    with me.box(style=me.Style(margin=me.Margin(top="12px", bottom="8px"))):
        for note in _NOTES:
            me.text(
                note,
                style=me.Style(
                    font_size="12px",
                    color=_MUTED,
                    margin=me.Margin(bottom="2px"),
                ),
            )
