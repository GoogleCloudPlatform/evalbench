"""Stats across trials for the viewer.

An agent run with num_trials above 1 writes run_stats.csv, with one row for
each scenario, and adds the stats of the suite to summary.csv. The Dashboard
tab shows the stats of the suite, and the Stability tab shows the scenarios.
A run without run_stats.csv has no Stability tab and no stats on the
Dashboard tab.

On the Stability tab, a click on a scenario row opens the result of each
trial of the scenario, from scores.csv.

Both tabs also show the min, median, and max of each numeric metric, such as
token_consumption: the Dashboard tab for the suite, and an open scenario row
for the scenario.
"""

import logging
import math
import os
from dataclasses import field
from typing import Any, Callable, Iterable, Optional

import mesop as me
import pandas as pd

import trial_details
from conversations import conversation_indexes

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
# The Dashboard tab has the stats of the suite only.
_STABILITY_HINT = "The Stability tab shows each scenario."

_GRID = "minmax(180px, 2fr) 90px minmax(190px, 1.5fr) 90px 120px 100px 110px"
# The label of each column of the scenario table, and the text of its tooltip.
_COLUMNS = (
    ("Scenario", ""),
    ("Status",
     "PASS: every trial was scored and passed. FAIL: every trial was "
     "scored and at least one failed. ERROR: at least one trial was "
     "unscored."),
    ("Trials passed",
     "Passed trials out of scored trials. An unscored trial has an agent "
     "error or a scorer error. It does not count as a pass or as a "
     "failure."),
    ("Pass rate", "Passed trials divided by scored trials."),
    ("95% interval",
     "The Wilson interval. It is the range that holds the true pass rate "
     "with 95% certainty."),
    ("Flakiness",
     "4p(1 - p) for the pass rate p. It is 0 if all scored trials agree "
     "and 1 at 50/50."),
    ("Confidence",
     "The chance, from 0 to 1, that the true pass rate is above 90%."),
)
_ROW_HINT = "Select a scenario to see the result of each trial."
# The tooltips of the metric cards.
_SUITE_METRICS_TIP = (
    "The median, min, and max of the trial averages. A trial average is "
    "the average of the metric over the scenarios in one trial. Only the "
    "scenarios with a scored result in each trial are in the average.")
_SCENARIO_METRICS_TIP = (
    "The median, min, and max of the metric over the scored trials of "
    "this scenario.")
# The trial details show at most this number of characters of an error.
_MAX_ERROR_LENGTH = 600


@me.stateclass
class RunStatsState:
    # The keys of the scenario rows with open trial details. See row_key.
    expanded: list[str] = field(default_factory=list)


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


def format_number(value: Any) -> str:
    """Formats the value of a numeric metric with thousands separators.

    A value below 100 that is not a whole number gets 1 decimal. The value
    has the unit of its scorer, and the text has no unit.
    """
    number = _number(value)
    if number is None:
        return _EMPTY
    if number.is_integer() or abs(number) >= 100:
        return f"{number:,.0f}"
    return f"{number:,.1f}"


def metric_names(columns: Iterable[str]) -> list[str]:
    """Returns the numeric metrics of run_stats.csv, in the column order.

    The file has the columns `<metric>_min`, `<metric>_median`, and
    `<metric>_max` for each numeric metric, such as token_consumption.
    """
    columns = list(columns)
    names = []
    for column in columns:
        name = column[:-len("_min")]
        if (column.endswith("_min") and f"{name}_median" in columns
                and f"{name}_max" in columns):
            names.append(name)
    return names


def _metric_row(
    name: Any, low: Any, median: Any, high: Any
) -> Optional[tuple[str, str, str, str]]:
    """Returns the name, min, median, and max of a metric as texts.

    Returns None for a metric without a value.
    """
    if all(_number(value) is None for value in (low, median, high)):
        return None
    return (str(name), format_number(low), format_number(median),
            format_number(high))


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
    names = metric_names(run_stats.columns)
    for record in run_stats.to_dict(orient="records"):
        metrics = (
            _metric_row(name, record.get(f"{name}_min"),
                        record.get(f"{name}_median"),
                        record.get(f"{name}_max"))
            for name in names)
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
            "num_trials": _count(record.get("num_trials")),
            # The numeric metrics that have a value for this scenario.
            "metrics": [metric for metric in metrics if metric],
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


def suite_metrics(
    summary_df: Optional[pd.DataFrame]
) -> list[tuple[str, str, str, str]]:
    """Returns the min, median, and max across trials of each numeric metric.

    The values come from the trial_min, trial_median, and trial_max columns
    of summary.csv. For each trial, the job takes the suite average of the
    metric. A metric without these values is not in the result.
    """
    columns = ("metric_name", "trial_min", "trial_median", "trial_max")
    if summary_df is None or not set(columns) <= set(summary_df.columns):
        return []
    metrics = (
        _metric_row(*(record[column] for column in columns))
        for record in summary_df.to_dict(orient="records"))
    return [metric for metric in metrics if metric]


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


def _label_with_tip(label: str, tip: str, style: me.Style) -> None:
    """Draws a label with an info icon. A tooltip opens on the two."""
    # The outer box keeps the tooltip area as narrow as the label and icon.
    with me.box(style=me.Style(display="flex")):
        with me.tooltip(message=tip, position="above"):
            with me.box(
                style=me.Style(
                    display="flex",
                    align_items="center",
                    gap="4px",
                    cursor="help",
                )
            ):
                me.text(label, style=style)
                me.icon(
                    "info",
                    style=me.Style(
                        font_size="14px",
                        width="14px",
                        height="14px",
                        color=_GREY,
                    ),
                )


def _card(
    label: str,
    value: str,
    note: str,
    color: str,
    tip: str = "",
    compact: bool = False,
) -> None:
    """Draws a card in the style of the Dashboard Summary cards.

    The card has a label, a value in large text, and a note below it. With
    `tip`, the label has a tooltip. A compact card has a smaller value, for
    the open scenario rows.
    """
    label_style = me.Style(
        font_weight="600", font_size="12px", color="#374151")
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
        with me.box(style=me.Style(margin=me.Margin(bottom="4px"))):
            if tip:
                _label_with_tip(label, tip, label_style)
            else:
                me.text(label, style=label_style)
        me.text(
            value,
            style=me.Style(
                font_weight="700",
                font_size="16px" if compact else "22px",
                color=color,
            ),
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
        min_width="1000px",
        padding=me.Padding.symmetric(vertical="10px", horizontal="16px"),
        border=me.Border(bottom=_line()),
        background="#f8fafc" if header else "#ffffff",
        # A click on a scenario row opens its trial details.
        cursor="default" if header else "pointer",
    )


def _chip_style(color: str) -> me.Style:
    """Returns the style of a small label with white text on `color`."""
    return me.Style(
        font_size="11px",
        font_weight="700",
        color="#ffffff",
        background=color,
        border_radius="999px",
        padding=me.Padding.symmetric(vertical="2px", horizontal="8px"),
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


def _column_header(label: str, tip: str) -> None:
    """Draws the header of a column.

    A column with a tooltip text gets an info icon. The tooltip opens when
    the pointer is on the label or on the icon.
    """
    style = me.Style(font_size="12px", font_weight="600", color=_MUTED)
    if not tip:
        # Only the Scenario column has no tooltip. The margin puts the label
        # above the scenario ids, which have an arrow icon before them.
        me.text(
            label,
            style=me.Style(
                font_size="12px",
                font_weight="600",
                color=_MUTED,
                margin=me.Margin(left="24px"),
            ),
        )
        return
    _label_with_tip(label, tip, style)


def row_key(results_dir: str, scenario_id: str) -> str:
    """Returns the key of a scenario row.

    The key has the name of the run directory, so a row that is open in one
    run is not open in a different run with the same scenario.
    """
    run = os.path.basename(os.path.normpath(results_dir))
    return f"{run}/{scenario_id}"


def _on_toggle_scenario(e: me.ClickEvent) -> None:
    """Opens the trial details of the clicked scenario row, or closes them."""
    state = me.state(RunStatsState)
    if e.key in state.expanded:
        state.expanded.remove(e.key)
    else:
        state.expanded.append(e.key)


def trial_score(trial: dict) -> tuple[str, str]:
    """Returns the score text of a trial and the color of the text.

    The colors are the same as in the Conversations tab: green for 100, red
    for 0, and amber between them. A trial without a score is gray. The
    color does not tell if the trial passed, because the pass limit of a job
    can be below 100.
    """
    score = trial["score"]
    if score is None:
        return "Unscored", _GREY
    if score >= 100:
        return f"Score {score:g}", _GREEN
    return f"Score {score:g}", _RED if score <= 0 else _AMBER


def _shorten(text: str) -> str:
    if len(text) <= _MAX_ERROR_LENGTH:
        return text
    return f"{text[:_MAX_ERROR_LENGTH]}…"


def _trial(
    trial: dict,
    conversation_index: Optional[int],
    on_open_conversation: Optional[Callable[[int], Callable]],
) -> None:
    """Draws the result of one trial.

    The result has the score, the errors, the verdict of the judge on each
    goal, and the summary of the judge. All texts come from scores.csv, and
    me.text escapes them.
    """
    score_text, score_color = trial_score(trial)
    small = me.Style(font_size="12px", color=_MUTED)
    with me.box(style=me.Style(margin=me.Margin(bottom="14px"))):
        with me.box(
            style=me.Style(
                display="flex",
                align_items="center",
                flex_wrap="wrap",
                gap="10px",
            )
        ):
            me.text(
                f"Trial {trial['trial_index'] + 1}",
                style=me.Style(
                    font_size="13px", font_weight="700", color=_TEXT),
            )
            me.text(score_text, style=_chip_style(score_color))
            me.text(trial["eval_id"], style=small)
            if on_open_conversation and conversation_index is not None:
                # The default button is 40px high with round ends. It is
                # too large next to the 12px text of the trial.
                me.button(
                    "Open conversation",
                    on_click=on_open_conversation(conversation_index),
                    type="stroked",
                    style=me.Style(
                        font_size="12px",
                        height="26px",
                        line_height="24px",
                        border_radius="6px",
                        padding=me.Padding.symmetric(
                            vertical="0", horizontal="10px"),
                    ),
                )
        # An error is the reason for an unscored trial, so it comes first.
        for label, error in (("Error", trial["run_error"]),
                             ("Scorer error", trial["scorer_error"])):
            if error:
                me.text(
                    f"{label}: {_shorten(error)}",
                    style=me.Style(
                        font_size="12px",
                        color=_RED,
                        margin=me.Margin(top="6px"),
                        overflow_wrap="anywhere",
                    ),
                )
        for goal in trial["goals"]:
            with me.box(
                style=me.Style(
                    display="flex",
                    align_items="flex-start",
                    gap="8px",
                    margin=me.Margin(top="6px"),
                )
            ):
                me.icon(
                    "check_circle" if goal["met"] else "cancel",
                    style=me.Style(
                        font_size="16px",
                        width="16px",
                        height="16px",
                        flex_shrink=0,
                        color=_GREEN if goal["met"] else _RED,
                    ),
                )
                with me.box():
                    me.text(
                        goal["goal"],
                        style=me.Style(
                            font_size="13px", font_weight="500", color=_TEXT),
                    )
                    if goal["reasoning"]:
                        me.text(goal["reasoning"], style=small)
        if trial["summary"]:
            me.text(
                f"Summary: {trial['summary']}",
                style=me.Style(
                    font_size="12px",
                    color=_MUTED,
                    margin=me.Margin(top="6px"),
                ),
            )


def metric_label(name: str) -> str:
    """Returns the name of a metric as words.

    For example, end_to_end_latency gives "End to end latency".
    """
    return name.replace("_", " ").strip().capitalize()


def _metric_cards(
    metrics: list[tuple], tip: str, compact: bool = False
) -> None:
    """Draws one card for each numeric metric.

    The card has the median in large text, and the min and the max below
    it. me.text escapes the metric name, which comes from a file.
    """
    with me.box(
        style=me.Style(
            display="grid",
            # 4 cards in each row, as the cards of the suite stats. A flex
            # row makes the cards of the last row wider than the others.
            grid_template_columns="repeat(4, minmax(0, 1fr))",
            gap="16px",
            width="100%",
            margin=me.Margin(bottom="16px"),
        )
    ):
        for name, low, median, high in metrics:
            _card(
                metric_label(name),
                median,
                f"Median. Min {low}, max {high}",
                _TEXT,
                tip=tip,
                compact=compact,
            )


def _trial_details(
    row: dict,
    trials: list[dict],
    conversation_index_of: dict[str, int],
    on_open_conversation: Optional[Callable[[int], Callable]],
) -> None:
    """Draws the metrics and the result of each trial, below a scenario row.
    """
    with me.box(
        style=me.Style(
            min_width="1000px",
            background="#f8fafc",
            padding=me.Padding(
                top="14px", right="16px", bottom="2px", left="40px"),
            border=me.Border(bottom=_line()),
        )
    ):
        if row["metrics"]:
            _metric_cards(
                row["metrics"], _SCENARIO_METRICS_TIP, compact=True)
        for trial in trials:
            _trial(
                trial,
                conversation_index_of.get(trial["eval_id"]),
                on_open_conversation,
            )
        missing = row["num_trials"] - len(trials)
        if missing > 0:
            me.text(
                f"scores.csv has no result for {missing} of "
                f"{row['num_trials']} trials.",
                style=me.Style(
                    font_size="12px",
                    color=_MUTED,
                    margin=me.Margin(bottom="12px"),
                ),
            )


def _table(
    rows: list[dict],
    open_keys: set[str],
    trials: dict[str, list[dict]],
    conversation_index_of: dict[str, int],
    on_open_conversation: Optional[Callable[[int], Callable]],
) -> None:
    """Draws the scenario table. A row in `open_keys` shows its trials."""
    cell = me.Style(font_size="13px", color=_TEXT)
    with me.box(
        style=me.Style(
            margin=me.Margin(top="16px", bottom="16px"),
            background="#ffffff",
            border_radius="10px",
            border=me.Border.all(_line()),
            box_shadow="0 1px 3px rgba(0,0,0,0.06)",
            overflow_x="auto",
        )
    ):
        with me.box(style=_row_style(header=True)):
            for label, tip in _COLUMNS:
                _column_header(label, tip)
        for row in rows:
            is_open = row["key"] in open_keys
            with me.box(
                style=_row_style(),
                key=row["key"],
                on_click=_on_toggle_scenario,
            ):
                with me.box(
                    style=me.Style(
                        display="flex", align_items="center", gap="6px")
                ):
                    me.icon(
                        "expand_more" if is_open else "chevron_right",
                        style=me.Style(
                            font_size="18px",
                            width="18px",
                            height="18px",
                            flex_shrink=0,
                            color=_MUTED,
                        ),
                    )
                    # me.text escapes the scenario id, which comes from a
                    # file.
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
                        style=_chip_style(
                            _STATUS_COLORS.get(row["status"], _GREY)),
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
            if is_open:
                _trial_details(
                    row,
                    trials.get(row["scenario_id"], []),
                    conversation_index_of,
                    on_open_conversation,
                )


def has_run_stats(results_dir: str) -> bool:
    """Tells if a run has stats across trials, and so a Stability tab."""
    return load_run_stats(results_dir) is not None


def _subtitle(text: str) -> None:
    me.text(
        text,
        style=me.Style(
            font_size="13px", color=_MUTED, margin=me.Margin(bottom="16px")),
    )


def suite_stats_component(
    results_dir: str, summary_df: Optional[pd.DataFrame] = None
) -> None:
    """Draws the stats of the suite across trials, for the Dashboard tab.

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
    _subtitle(f"{trials_summary(run_stats)}. {_STABILITY_HINT}")
    with me.box(
        style=me.Style(
            display="flex",
            flex_direction="row",
            flex_wrap="wrap",
            gap="16px",
            width="100%",
            margin=me.Margin(bottom="16px"),
        )
    ):
        for card in suite_cards(suite_stats(summary_df), rows):
            _card(*card)
    metrics = suite_metrics(summary_df)
    if metrics:
        _metric_cards(metrics, _SUITE_METRICS_TIP)


def stability_component(
    results_dir: str,
    on_open_conversation: Optional[Callable[[int], Callable]] = None,
) -> None:
    """Draws the Stability tab: the stats of each scenario across trials.

    A click on a scenario row opens the result of each trial.

    `on_open_conversation` takes the index of a conversation and returns the
    click handler that opens it. Without it, the trial details have no
    button for the conversation.
    """
    run_stats = load_run_stats(results_dir)
    if run_stats is None:
        me.text("This run has no stats across trials.")
        return
    rows = scenario_rows(run_stats)
    for row in rows:
        row["key"] = row_key(results_dir, row["scenario_id"])
    open_keys = (set(me.state(RunStatsState).expanded)
                 & {row["key"] for row in rows})
    # scores.csv and evals.csv can be large. Read them only for an open row.
    trials = trial_details.load_trials(results_dir) if open_keys else {}
    conversation_index_of = (
        conversation_indexes(results_dir)
        if open_keys and on_open_conversation else {})

    me.text(
        "Stability",
        style=me.Style(
            font_size="24px",
            font_weight="bold",
            margin=me.Margin(bottom="4px"),
        ),
    )
    _subtitle(f"{trials_summary(run_stats)}. {_ROW_HINT}")
    _table(rows, open_keys, trials, conversation_index_of,
           on_open_conversation)
