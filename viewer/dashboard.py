import mesop as me


import pandas as pd
import os


def dashboard_component(results_dir: str):
    me.text(
        "Dashboard Summary",
        style=me.Style(
            font_size="24px", font_weight="bold", margin=me.Margin(bottom="20px")
        ),
    )

    summary_path = os.path.join(results_dir, "summary.csv")
    if not os.path.exists(summary_path):
        me.text(f"No summary data found in {results_dir}")
        return

    try:
        df = pd.read_csv(summary_path)
    except Exception as e:
        me.text(f"Error reading summary data: {e}")
        return

    with me.box(
        style=me.Style(
            display="flex",
            flex_direction="row",
            flex_wrap="wrap",
            gap="16px",
            width="100%",
        )
    ):
        for _, row in df.iterrows():
            metric = row.get("metric_name", "Unknown")
            correct = row.get("correct_results_count", 0)
            total = row.get("total_results_count", 0)

            pct = (correct / total) * 100 if total > 0 else 0
            color = (
                "#10b981"
                if pct >= 80
                else ("#ef4444" if pct < 40 else "#f59e0b")
            )

            with me.box(
                style=me.Style(
                    width="calc(33.333% - 11px)",
                    background="#ffffff",
                    border_radius="10px",
                    border=me.Border.all(
                        me.BorderSide(width="1px", color="#e5e7eb", style="solid")
                    ),
                    padding=me.Padding.all("12px"),
                    box_shadow="0 1px 3px rgba(0,0,0,0.06)",
                )
            ):
                me.text(
                    metric,
                    style=me.Style(
                        font_weight="600",
                        font_size="12px",
                        color="#374151",
                        margin=me.Margin(bottom="4px"),
                    ),
                )
                me.text(
                    f"{correct}/{total}",
                    style=me.Style(
                        font_weight="700",
                        font_size="22px",
                        color=color,
                    ),
                )
