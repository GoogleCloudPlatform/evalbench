import mesop as me
import os
import pandas as pd
from typing import Callable
import logging


def conversations_component(
    results_dir: str,
    conversation_index: int = 0,
    on_prev: Callable | None = None,
    on_next: Callable | None = None,
):
    me.text(
        "Conversations",
        style=me.Style(
            font_size="24px", font_weight="bold", margin=me.Margin(bottom="20px")
        ),
    )

    evals_path = os.path.join(results_dir, "evals.csv")

    if os.path.exists(evals_path):
        import json

        try:
            df = pd.read_csv(evals_path)
            if "conversation_history" in df.columns:
                histories = df["conversation_history"].dropna().tolist()

                if not histories:
                    me.text(
                        "The evals.csv file does not contain any valid 'conversation_history' entries."
                    )
                else:
                    total = len(histories)
                    idx = max(0, min(conversation_index, total - 1))

                    eval_id = (
                        df["eval_id"].iloc[idx] if "eval_id" in df.columns else str(idx)
                    )

                    # Navigation header
                    with me.box(
                        style=me.Style(
                            display="flex",
                            flex_direction="row",
                            align_items="center",
                            gap="12px",
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

                    history_str = histories[idx]

                    # Side-by-side: chat (left, flex:1) | scores 3-col (right, 40%)
                    with me.box(
                        style=me.Style(
                            display="flex",
                            flex_direction="row",
                            gap="20px",
                            align_items="flex-start",
                        )
                    ):
                        # --- Chat (left) ---
                        with me.box(
                            style=me.Style(
                                flex="1",
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
                                            with me.tooltip(message=stats_str, position="right", disabled=not bool(stats_str)):
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
                                                    me.markdown(agent_content)
                            except Exception as parse_e:
                                me.text(f"Error parsing JSON: {parse_e}")
                                me.code(history_str)

                        # --- Scores panel (right, 3 columns) ---
                        with me.box(
                            style=me.Style(
                                width="40%",
                                flex_shrink="0",
                                display="flex",
                                flex_direction="column",
                                gap="8px",
                            )
                        ):
                            scores_path = os.path.join(results_dir, "scores.csv")
                            if os.path.exists(scores_path):
                                try:
                                    scores_df = pd.read_csv(scores_path)
                                    if "id" in scores_df.columns:
                                        row_scores = scores_df[
                                            scores_df["id"] == eval_id
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
                                                            elif comparator == "token_consumption":
                                                                unit = "tokens"
                                                            elif comparator == "turn_count":
                                                                unit = "turns"
                                                            elif comparator in ("trajectory_matcher", "goal_completion", "parameter_analysis", "behavioral_metrics"):
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
