import os
import mesop as me
import pandas as pd
import yaml
import logging

logging.basicConfig(level=logging.INFO)

try:
    import dashboard
    import conversations
except ImportError:
    # Optional modules could not be imported; continue without them.
    logging.warning(
        "Optional modules 'dashboard', and 'conversations' "
        "could not be imported (absolute or relative)."
    )


def df_to_config(df: pd.DataFrame) -> dict:
    import ast

    original_dict = {}

    for _, row in df.iterrows():
        key_path = row["config"]
        value_str = row["value"]

        try:
            if pd.isna(value_str):
                value = None
            else:
                value = ast.literal_eval(value_str)
        except (ValueError, SyntaxError, TypeError):
            value = value_str

        keys = key_path.split(".")

        current_level = original_dict
        for key in keys[:-1]:
            if key not in current_level:
                current_level[key] = {}
            current_level = current_level[key]

        current_level[keys[-1]] = value

    return original_dict


@me.stateclass
class State:
    selected_directory: str
    selected_tab: str = "Dashboard"
    conversation_index: int = 0


@me.page(
    path="/",
    title="Evalbench",
    stylesheets=[
        "data:",
        "data:text/css;charset=utf-8,"
        ".mdc-tooltip__surface%20%7B%0A"
        "%20%20max-height%3A%20none%20%21important%3B%0A"
        "%20%20max-width%3A%20none%20%21important%3B%0A"
        "%20%20white-space%3A%20pre-wrap%20%21important%3B%0A"
        "%7D",
    ],
)
def app():
    state = me.state(State)

    # Check multiple locations for results directory
    results_dir_candidates = [
        "/tmp_session_files/results",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "results"),
        os.path.join(os.getcwd(), "results"),
    ]

    results_dir = None
    for candidate in results_dir_candidates:
        if os.path.exists(candidate) and os.path.isdir(candidate):
            results_dir = candidate
            break

    if results_dir is None:
        results_dir = results_dir_candidates[1]  # Fallback to default

    directories = []
    if os.path.exists(results_dir):
        # List directories only
        directories = [
            d
            for d in os.listdir(results_dir)
            if os.path.isdir(os.path.join(results_dir, d))
        ]

    def on_selection_change(e: me.SelectSelectionChangeEvent):
        state.selected_directory = e.value
        state.conversation_index = 0

    # Full-width header bar
    with me.box(
        style=me.Style(
            background="#1e293b",
            padding=me.Padding.symmetric(vertical="16px", horizontal="5%"),
            margin=me.Margin(bottom="24px"),
        )
    ):
        me.text(
            "EvalBench Viewer",
            style=me.Style(
                color="#f8fafc",
                font_size="22px",
                font_weight="700",
                letter_spacing="0.5px",
            ),
        )

    # Centered content at 90% browser width
    with me.box(
        style=me.Style(
            width="90%",
            margin=me.Margin.symmetric(horizontal="auto"),
            display="flex",
            flex_direction="column",
            gap="16px",
        )
    ):
        with me.box(
            style=me.Style(width="100%", max_width="400px", margin=me.Margin(bottom="8px"))
        ):
            me.select(
                label="Select a result directory",
                options=[
                    me.SelectOption(label=d, value=d) for d in sorted(directories)
                ],
                on_selection_change=on_selection_change,
                value=state.selected_directory,
                appearance="outline",
            )

        if state.selected_directory:

            def on_tab_change(e: me.ButtonToggleChangeEvent):
                state.selected_tab = e.value

            me.button_toggle(
                value=state.selected_tab,
                buttons=[
                    me.ButtonToggleButton(label="Dashboard", value="Dashboard"),
                    me.ButtonToggleButton(label="Configs", value="Configs"),
                    # me.ButtonToggleButton(label="Evals", value="Evals"),
                    # me.ButtonToggleButton(label="Scores", value="Scores"),
                    me.ButtonToggleButton(
                        label="Conversations", value="Conversations"
                    ),
                    # me.ButtonToggleButton(label="Summary", value="Summary"),
                ],
                on_change=on_tab_change,
            )

            if state.selected_tab == "Dashboard":
                dashboard.dashboard_component(
                    os.path.join(results_dir, state.selected_directory)
                )
            elif state.selected_tab == "Conversations":

                def on_prev_conversation(e: me.ClickEvent):
                    s = me.state(State)
                    if s.conversation_index > 0:
                        s.conversation_index -= 1

                def on_next_conversation(e: me.ClickEvent):
                    s = me.state(State)
                    s.conversation_index += 1

                conversations.conversations_component(
                    os.path.join(results_dir, state.selected_directory),
                    conversation_index=state.conversation_index,
                    on_prev=on_prev_conversation,
                    on_next=on_next_conversation,
                )
            elif state.selected_tab == "Configs":
                config_path = os.path.join(
                    results_dir, state.selected_directory, "configs.csv"
                )
                if os.path.exists(config_path):
                    try:
                        df = pd.read_csv(config_path)
                        config = df_to_config(df)
                        me.code(yaml.dump(config))
                    except Exception as e:
                        me.text(f"Error reading configs.csv: {e}")
                else:
                    me.text(f"configs.csv not found in {state.selected_directory}")
            elif state.selected_tab == "Evals":
                evals_path = os.path.join(
                    results_dir, state.selected_directory, "evals.csv"
                )
                if os.path.exists(evals_path):
                    try:
                        df = pd.read_csv(evals_path)
                        me.table(data_frame=df)
                    except Exception as e:
                        me.text(f"Error reading evals.csv: {e}")
                else:
                    me.text(f"evals.csv not found in {state.selected_directory}")
            elif state.selected_tab == "Scores":
                scores_path = os.path.join(
                    results_dir, state.selected_directory, "scores.csv"
                )
                if os.path.exists(scores_path):
                    try:
                        df = pd.read_csv(scores_path)
                        me.table(data_frame=df)
                    except Exception as e:
                        me.text(f"Error reading scores.csv: {e}")
                else:
                    me.text(f"scores.csv not found in {state.selected_directory}")
            elif state.selected_tab == "Summary":
                summary_path = os.path.join(
                    results_dir, state.selected_directory, "summary.csv"
                )
                if os.path.exists(summary_path):
                    try:
                        df = pd.read_csv(summary_path)
                        me.table(data_frame=df)
                    except Exception as e:
                        me.text(f"Error reading summary.csv: {e}")
                else:
                    me.text(f"summary.csv not found in {state.selected_directory}")


if __name__ == "__main__":
    me.run(app)
