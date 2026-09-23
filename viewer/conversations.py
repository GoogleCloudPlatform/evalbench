try:
    import mesop as me
except ImportError:
    me = None
import os
import pandas as pd
from typing import Callable
import logging


COUNTER_METRICS = {
    "turn_count",
    "agent_steps",
    "end_to_end_latency",
    "tool_call_latency",
    "token_consumption",
    "tokens_processed",
    "effective_billed_tokens",
}

COUNTER_UNITS = {
    "ms",
    "s",
    "tokens",
    "turns",
    "steps",
    "count",
    "bytes",
}


def _is_percentage_metric(comp: str, unit: str) -> bool:
    if unit == "%":
        return True
    if comp in COUNTER_METRICS or unit in COUNTER_UNITS:
        return False
    return bool(comp)


def _get_conversation_status(
    scores_df: pd.DataFrame | None, eval_id: str
) -> tuple[str, list[str]]:
    """Classifies conversation status based on scores.csv metrics.

    Returns:
        tuple[str, list[str]]: (status, list_of_failed_metric_strings)
        status is one of:
        - "red": goal_completion < 100%
        - "yellow": goal_completion == 100% (or N/A), but another evaluative metric < 100%
        - "green": all evaluated percentage metrics == 100%
        - "neutral": no evaluative scores available
    """
    if scores_df is None or scores_df.empty or "id" not in scores_df.columns:
        return "neutral", []

    row_scores = scores_df[scores_df["id"].astype(str) == str(eval_id)]
    if row_scores.empty:
        return "neutral", []

    has_goal_completion_fail = False
    has_other_fail = False
    has_any_eval_metric = False
    failed_details = []

    for _, s_row in row_scores.iterrows():
        comp = str(s_row.get("comparator", "")).strip()
        raw_score = s_row.get("score", None)
        unit = str(s_row.get("unit", "")).strip()
        if unit == "nan":
            unit = ""

        if pd.isna(raw_score):
            continue
        try:
            val = float(raw_score)
        except (ValueError, TypeError):
            continue

        if not _is_percentage_metric(comp, unit):
            continue

        has_any_eval_metric = True
        if val < 100.0:
            failed_details.append(f"{comp}: {val:.0f}%")
            if comp == "goal_completion":
                has_goal_completion_fail = True
            else:
                has_other_fail = True

    if has_goal_completion_fail:
        return "red", failed_details
    if has_other_fail:
        return "yellow", failed_details
    if has_any_eval_metric:
        return "green", []
    return "neutral", []


def conversations_component(
    results_dir: str,
    conversation_index: int = 0,
    on_prev: Callable | None = None,
    on_next: Callable | None = None,
    on_select: Callable[[int], Callable] | None = None,
):
    me.text(
        "Conversations",
        style=me.Style(
            font_size="24px", font_weight="bold", margin=me.Margin(bottom="20px")
        ),
    )

    evals_path = os.path.join(results_dir, "evals.csv")
    scores_path = os.path.join(results_dir, "scores.csv")

    scores_df = None
    if os.path.exists(scores_path):
        try:
            scores_df = pd.read_csv(scores_path)
        except Exception as e:
            logging.warning(f"Failed to read scores.csv in conversations_component: {e}")

    if os.path.exists(evals_path):
        import json

        try:
            df = pd.read_csv(evals_path)
            if "conversation_history" in df.columns:
                valid_df = df[df["conversation_history"].notna()].reset_index(drop=True)
                histories = valid_df["conversation_history"].tolist()

                if not histories:
                    me.text(
                        "The evals.csv file does not contain any valid 'conversation_history' entries."
                    )
                else:
                    total = len(histories)
                    idx = max(0, min(conversation_index, total - 1))

                    def _get_eval_id(row_idx: int) -> str:
                        if "eval_id" in valid_df.columns:
                            return str(valid_df["eval_id"].iloc[row_idx])
                        if "id" in valid_df.columns:
                            return str(valid_df["id"].iloc[row_idx])
                        return str(row_idx)

                    eval_id = _get_eval_id(idx)

                    # Precompute status for all conversations in this run
                    statuses = []
                    counts = {"red": 0, "yellow": 0, "green": 0, "neutral": 0}
                    for i in range(total):
                        e_id = _get_eval_id(i)
                        st_color, st_details = _get_conversation_status(scores_df, e_id)
                        statuses.append((st_color, st_details, e_id))
                        counts[st_color] = counts.get(st_color, 0) + 1

                    cur_status, cur_failed_details, _ = statuses[idx]

                    # Legend and Page Selector Bar
                    with me.box(
                        style=me.Style(
                            display="flex",
                            flex_direction="column",
                            gap="10px",
                            padding=me.Padding.all("16px"),
                            background="#f8fafc",
                            border_radius="10px",
                            border=me.Border.all(
                                me.BorderSide(width="1px", color="#e2e8f0", style="solid")
                            ),
                            margin=me.Margin(bottom="16px"),
                        )
                    ):
                        # Top row: Legend & summary counts
                        with me.box(
                            style=me.Style(
                                display="flex",
                                flex_direction="row",
                                justify_content="space-between",
                                align_items="center",
                                flex_wrap="wrap",
                                gap="12px",
                            )
                        ):
                            me.text(
                                "Select Conversation Page:",
                                style=me.Style(
                                    font_weight="600",
                                    font_size="14px",
                                    color="#334155",
                                ),
                            )
                            with me.box(
                                style=me.Style(
                                    display="flex",
                                    flex_direction="row",
                                    gap="16px",
                                    align_items="center",
                                    flex_wrap="wrap",
                                )
                            ):
                                # Red Legend
                                with me.box(
                                    style=me.Style(
                                        display="flex",
                                        align_items="center",
                                        gap="6px",
                                    )
                                ):
                                    me.box(
                                        style=me.Style(
                                            width="12px",
                                            height="12px",
                                            border_radius="3px",
                                            background="#fee2e2",
                                            border=me.Border.all(
                                                me.BorderSide(
                                                    width="1px",
                                                    color="#dc2626",
                                                    style="solid",
                                                )
                                            ),
                                        )
                                    )
                                    me.text(
                                        f"<100% Goal Completion ({counts['red']})",
                                        style=me.Style(font_size="12px", color="#475569"),
                                    )
                                # Yellow Legend
                                with me.box(
                                    style=me.Style(
                                        display="flex",
                                        align_items="center",
                                        gap="6px",
                                    )
                                ):
                                    me.box(
                                        style=me.Style(
                                            width="12px",
                                            height="12px",
                                            border_radius="3px",
                                            background="#fef3c7",
                                            border=me.Border.all(
                                                me.BorderSide(
                                                    width="1px",
                                                    color="#d97706",
                                                    style="solid",
                                                )
                                            ),
                                        )
                                    )
                                    me.text(
                                        f"Other Metric Issue ({counts['yellow']})",
                                        style=me.Style(font_size="12px", color="#475569"),
                                    )
                                # Green Legend
                                with me.box(
                                    style=me.Style(
                                        display="flex",
                                        align_items="center",
                                        gap="6px",
                                    )
                                ):
                                    me.box(
                                        style=me.Style(
                                            width="12px",
                                            height="12px",
                                            border_radius="3px",
                                            background="#dcfce7",
                                            border=me.Border.all(
                                                me.BorderSide(
                                                    width="1px",
                                                    color="#16a34a",
                                                    style="solid",
                                                )
                                            ),
                                        )
                                    )
                                    me.text(
                                        f"Passed ({counts['green']})",
                                        style=me.Style(font_size="12px", color="#475569"),
                                    )

                        # Pill buttons row (wraps ~32 pills per row; scrolls cleanly if >120 conversations)
                        with me.box(
                            style=me.Style(
                                display="flex",
                                flex_direction="row",
                                flex_wrap="wrap",
                                gap="6px",
                                max_height="160px",
                                overflow_y="auto",
                                padding=me.Padding.symmetric(vertical="2px"),
                            )
                        ):
                            for i in range(total):
                                st_color, st_details, st_eval_id = statuses[i]
                                is_active = i == idx

                                if st_color == "red":
                                    bg = "#dc2626" if is_active else "#fee2e2"
                                    fg = "#ffffff" if is_active else "#991b1b"
                                    bdr_col = "#7f1d1d" if is_active else "#f87171"
                                elif st_color == "yellow":
                                    bg = "#d97706" if is_active else "#fef3c7"
                                    fg = "#ffffff" if is_active else "#92400e"
                                    bdr_col = "#78350f" if is_active else "#fbbf24"
                                elif st_color == "green":
                                    bg = "#16a34a" if is_active else "#dcfce7"
                                    fg = "#ffffff" if is_active else "#166534"
                                    bdr_col = "#14532d" if is_active else "#4ade80"
                                else:
                                    bg = "#374151" if is_active else "#f3f4f6"
                                    fg = "#ffffff" if is_active else "#374151"
                                    bdr_col = "#111827" if is_active else "#d1d5db"

                                bdr_width = "2px" if is_active else "1px"
                                shadow = (
                                    "0 2px 4px rgba(0,0,0,0.18)"
                                    if is_active
                                    else "none"
                                )

                                tooltip_msg = (
                                    f"ID: {st_eval_id} ({', '.join(st_details)})"
                                    if st_details
                                    else f"ID: {st_eval_id}"
                                )
                                click_fn = on_select(i) if on_select else None
                                with me.tooltip(
                                    message=tooltip_msg, position="above"
                                ):
                                    me.button(
                                        str(i + 1),
                                        on_click=click_fn,
                                        disabled=(click_fn is None),
                                        style=me.Style(
                                            min_width="36px",
                                            height="32px",
                                            padding=me.Padding.symmetric(
                                                vertical="4px", horizontal="8px"
                                            ),
                                            background=bg,
                                            color=fg,
                                            font_weight=(
                                                "700" if is_active else "600"
                                            ),
                                            font_size="13px",
                                            border_radius="6px",
                                            border=me.Border.all(
                                                me.BorderSide(
                                                    width=bdr_width,
                                                    color=bdr_col,
                                                    style="solid",
                                                )
                                            ),
                                            box_shadow=shadow,
                                            cursor="pointer",
                                        ),
                                    )

                    # Navigation header
                    with me.box(
                        style=me.Style(
                            display="flex",
                            flex_direction="row",
                            align_items="center",
                            gap="12px",
                            flex_wrap="wrap",
                            margin=me.Margin(bottom="16px"),
                        )
                    ):
                        me.button(
                            "←",
                            on_click=on_prev,
                            disabled=(idx == 0 or on_prev is None),
                            style=me.Style(font_size="20px"),
                        )
                        me.text(
                            f"Conversation {idx + 1} of {total}  (Eval ID: {eval_id})",
                            style=me.Style(color="#4b5563", font_weight="500"),
                        )
                        me.button(
                            "→",
                            on_click=on_next,
                            disabled=(idx == total - 1 or on_next is None),
                            style=me.Style(font_size="20px"),
                        )

                        # Status badge for the active conversation
                        if cur_status == "red":
                            badge_bg = "#fee2e2"
                            badge_fg = "#991b1b"
                            badge_bdr = "#f87171"
                            badge_txt = (
                                f"Goal Completion Issue ({', '.join(cur_failed_details)})"
                                if cur_failed_details
                                else "Goal Completion < 100%"
                            )
                        elif cur_status == "yellow":
                            badge_bg = "#fef3c7"
                            badge_fg = "#92400e"
                            badge_bdr = "#fbbf24"
                            badge_txt = (
                                f"Metric Issue ({', '.join(cur_failed_details)})"
                                if cur_failed_details
                                else "Other Metric < 100%"
                            )
                        elif cur_status == "green":
                            badge_bg = "#dcfce7"
                            badge_fg = "#166534"
                            badge_bdr = "#4ade80"
                            badge_txt = "Passed (All Metrics 100%)"
                        else:
                            badge_bg = "#f3f4f6"
                            badge_fg = "#4b5563"
                            badge_bdr = "#d1d5db"
                            badge_txt = "No Scores Recorded"

                        with me.box(
                            style=me.Style(
                                background=badge_bg,
                                color=badge_fg,
                                padding=me.Padding.symmetric(
                                    vertical="4px", horizontal="10px"
                                ),
                                border_radius="9999px",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px", color=badge_bdr, style="solid"
                                    )
                                ),
                                font_size="12px",
                                font_weight="600",
                            )
                        ):
                            me.text(badge_txt)

                    history_str = histories[idx]

                    # Side-by-side: chat (left, flex:1) | scores 3-col (right, 40%)
                    with me.box(
                        style=me.Style(
                            display="flex",
                            flex_direction="row",
                            gap="20px",
                            align_items="flex-start",
                            flex_wrap="wrap",
                        )
                    ):
                        # --- Chat (left) ---
                        with me.box(
                            style=me.Style(
                                flex="1",
                                min_width="400px",
                                display="flex",
                                flex_direction="column",
                                gap="16px",
                                padding=me.Padding.all("20px"),
                                background="#f9fafb",
                                border_radius="12px",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px", color="#e5e7eb", style="solid"
                                    )
                                ),
                            )
                        ):
                            try:
                                history_list = json.loads(history_str)
                                for turn in history_list:
                                    if "user" in turn:
                                        with me.box(
                                            style=me.Style(
                                                display="flex",
                                                justify_content="flex-end",
                                                width="100%",
                                            )
                                        ):
                                            with me.box(
                                                style=me.Style(
                                                    background="#3b82f6",
                                                    color="#ffffff",
                                                    padding=me.Padding.symmetric(
                                                        vertical="12px",
                                                        horizontal="16px",
                                                    ),
                                                    border_radius="12px",
                                                    max_width="80%",
                                                    box_shadow="0 1px 2px 0 rgba(0,0,0,0.05)",
                                                )
                                            ):
                                                me.markdown(turn["user"])

                                    if "agent" in turn:
                                        agent_content = turn["agent"]
                                        stats_str = ""
                                        try:
                                            agent_data = json.loads(agent_content)
                                            if "stats" in agent_data:
                                                stats_str = json.dumps(agent_data["stats"], indent=2)
                                            if "response" in agent_data:
                                                agent_content = agent_data["response"]
                                        except Exception:
                                            # If the agent content is not valid JSON, fall back to displaying the raw content
                                            # and skip stats; log at debug level for troubleshooting without breaking the UI.
                                            logging.debug(
                                                "Failed to parse agent content as JSON; using raw content. Content: %r",
                                                agent_content,
                                            )

                                        with me.box(
                                            style=me.Style(
                                                display="flex",
                                                justify_content="flex-start",
                                                width="100%",
                                            )
                                        ):
                                            with me.box(
                                                style=me.Style(
                                                    background="#ffffff",
                                                    color="#1f2937",
                                                    padding=me.Padding.symmetric(
                                                        vertical="12px",
                                                        horizontal="16px",
                                                    ),
                                                    border_radius="12px",
                                                    border=me.Border.all(
                                                        me.BorderSide(
                                                            width="1px",
                                                            color="#e5e7eb",
                                                            style="solid",
                                                        )
                                                    ),
                                                    max_width="80%",
                                                    box_shadow="0 1px 2px 0 rgba(0,0,0,0.05)",
                                                    overflow_x="auto",
                                                )
                                            ):
                                                me.text(
                                                    "Agent",
                                                    style=me.Style(
                                                        font_weight="bold",
                                                        font_size="12px",
                                                        color="#6b7280",
                                                        margin=me.Margin(bottom="4px"),
                                                    ),
                                                )
                                                if agent_content:
                                                    me.markdown(agent_content)
                                                else:
                                                    me.text(
                                                        "Empty response",
                                                        style=me.Style(
                                                            color="#94a3b8",
                                                            font_style="italic",
                                                            font_size="14px",
                                                        ),
                                                    )

                                                if stats_str:
                                                    with me.expansion_panel(title="Stats", expanded=False):
                                                        me.code(stats_str)
                            except Exception as parse_e:
                                me.text(f"Error parsing JSON: {parse_e}")
                                me.code(history_str)

                        # --- Scores panel (right, 3 columns) ---
                        with me.box(
                            style=me.Style(
                                width="40%",
                                min_width="300px",
                                flex_shrink="0",
                                display="flex",
                                flex_direction="column",
                                gap="8px",
                            )
                        ):
                            # Conversation Plan
                            scenario_str = (
                                valid_df["scenario"].iloc[idx]
                                if "scenario" in valid_df.columns
                                else ""
                            )
                            conversation_plan = ""
                            if scenario_str and pd.notna(scenario_str):
                                try:
                                    scenario_data = json.loads(scenario_str)
                                    conversation_plan = scenario_data.get("conversation_plan", "")
                                except Exception:
                                    try:
                                        import ast
                                        scenario_data = ast.literal_eval(scenario_str)
                                        conversation_plan = scenario_data.get("conversation_plan", "")
                                    except Exception as e:
                                        logging.warning(f"Failed to parse scenario: {e}")

                            if conversation_plan:
                                with me.expansion_panel(title="Conversation Plan", expanded=True):
                                    if isinstance(conversation_plan, list):
                                        conversation_plan = "\n".join([str(x) for x in conversation_plan])
                                    me.markdown(str(conversation_plan))

                            if scores_df is not None and "id" in scores_df.columns:
                                try:
                                    row_scores = scores_df[
                                        scores_df["id"].astype(str) == str(eval_id)
                                    ]
                                    if not row_scores.empty:
                                        with me.expansion_panel(title="Scores", expanded=True):
                                            with me.box(
                                                style=me.Style(
                                                    display="flex",
                                                    flex_direction="column",
                                                    gap="8px",
                                                )
                                            ):
                                                for (
                                                    _,
                                                    score_row,
                                                ) in row_scores.iterrows():
                                                    comparator = score_row.get(
                                                        "comparator", "metric"
                                                    )
                                                    score = score_row.get("score", None)
                                                    unit = score_row.get("unit", "")
                                                    if not unit or str(unit) == "nan":
                                                        if comparator in ("end_to_end_latency", "tool_call_latency"):
                                                            unit = "ms"
                                                        elif comparator in ("token_consumption", "tokens_processed", "effective_billed_tokens"):
                                                            unit = "tokens"
                                                        elif comparator == "turn_count":
                                                            unit = "turns"
                                                        elif comparator == "agent_steps":
                                                            unit = "steps"
                                                        elif _is_percentage_metric(str(comparator), ""):
                                                            unit = "%"
                                                    logs = score_row.get(
                                                        "comparison_logs", ""
                                                    )
                                                    score_val = (
                                                        float(score)
                                                        if pd.notna(score)
                                                        else None
                                                    )
                                                    unit_str = f" {unit}" if unit and str(unit) != "nan" else ""
                                                    score_str = f"{score_val:.0f}{unit_str}" if score_val is not None else ""

                                                    # Full width for each score, now collapsible
                                                    with me.expansion_panel(
                                                        title=comparator,
                                                        description=score_str,
                                                        style=me.Style(
                                                            width="100%",
                                                            background="#ffffff",
                                                            border_radius="10px",
                                                            border=me.Border.all(
                                                                me.BorderSide(
                                                                    width="1px",
                                                                    color="#e5e7eb",
                                                                    style="solid",
                                                                )
                                                            ),
                                                            box_shadow="0 1px 3px rgba(0,0,0,0.06)",
                                                        )
                                                    ):
                                                        if logs and str(logs) != "nan":
                                                            with me.box(style=me.Style(padding=me.Padding.all("12px"))):
                                                                # Render logs inside the expansion panel when opened
                                                                me.markdown(logs)
                                except Exception as scores_e:
                                    me.text(f"Error reading scores: {scores_e}")

            else:
                me.text(
                    "The evals.csv file does not contain a 'conversation_history' column."
                )
        except Exception as e:
            me.text(f"Error reading evals.csv: {e}")
    else:
        me.text(f"No evals.csv file found in {results_dir}.")
