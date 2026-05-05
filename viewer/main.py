import os
import mesop as me
import pandas as pd
import yaml
import logging
import json
import subprocess
import precompute_trends
from summarizer import summarize_eval_scoring
from ai_comparer import compare_evals

@me.stateclass
class State:
    selected_directory: str = ""
    selected_tab: str = "Dashboard"
    conversation_index: int = 0
    eval_summaries: str = ""
    eval_id_filter: str = ""
    product_filter: str = ""
    requester_filter: str = ""
    dataset_filter: str = ""
    sort_column: str = "date"
    sort_descending: bool = True
    open_dropdown: str = ""
    selected_main_tab: str = "Status"
    trends_product_filter: str = ""
    cache_cleared_message: str = ""
    ai_summary: str = ""
    is_summarizing: bool = False
    ai_score: float = 0.0
    status_agent_tab: str = "Gemini"
    list_agent_tab: str = "Gemini"
    trends_agent_tab: str = "Gemini"
    show_formula: bool = False
    rows_to_show: int = 10
    selected_evals: str = "[]"
    base_product: str = ""
    base_dataset: str = ""
    compare_tab_visible: bool = False
    compare_evals: str = "[]"
    select_mode_active: bool = False
    ai_comparison: str = ""

try:
    # Try to read version from file (created during build)
    version_file = os.path.join(os.path.dirname(__file__), "version.txt")
    if os.path.exists(version_file):
        with open(version_file, "r") as f:
            GIT_VERSION = f.read().strip()
    else:
        # Fallback to git command (for local dev)
        GIT_VERSION = subprocess.check_output(
            ["git", "rev-parse", "--short", "HEAD"],
            stderr=subprocess.DEVNULL
        ).decode("utf-8").strip()
except Exception:
    GIT_VERSION = "unknown"


logging.basicConfig(level=logging.INFO)

# Manually enable debug mode to bypass XSRF check if needed
# (e.g. when running in container behind a proxy)
if os.environ.get("MESOP_XSRF_CHECK") == "false":
    try:
        from mesop.runtime import runtime
        runtime().debug_mode = True
    except Exception as e:
        logging.error(f"Failed to enable debug mode: {e}")

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


def get_eval_details(results_dir, dir_name):
    details = {
        "product": "N/A",
        "date": "N/A",
        "requester": "N/A",
        "exact_match": "N/A",
        "llmrater": "N/A",
        "trajectory_matcher": "N/A",
        "turn_count": "N/A",
        "executable": "N/A",
        "token_consumption": "N/A",
        "end_to_end_latency": "N/A",
    }

    # Get product
    config_path = os.path.join(results_dir, dir_name, "configs.csv")
    if os.path.exists(config_path):
        try:
            df = pd.read_csv(config_path)
            # Check for both typo and correct spelling
            row = df[
                df["config"].isin(
                    [
                        "experiment_config.poduct_name",
                        "experiment_config.product_name",
                    ]
                )
            ]
            if not row.empty:
                details["product"] = str(row["value"].iloc[0])

            # Check for requester
            req_row = df[
                df["config"].isin(
                    [
                        "experiment_config.experiment_config.guitar_requester",
                        "experiment_config.guitar_requester",
                    ]
                )
            ]
            if not req_row.empty:
                details["requester"] = str(req_row["value"].iloc[0])
        except Exception as e:
            logging.warning(f"Error reading configs.csv for {dir_name}: {e}")

    # Get summary metrics
    summary_path = os.path.join(results_dir, dir_name, "summary.csv")
    if os.path.exists(summary_path):
        try:
            df = pd.read_csv(summary_path)
            if "run_time" in df.columns and not df.empty:
                details["date"] = str(df["run_time"].iloc[0])
            for _, row in df.iterrows():
                name = row.get("metric_name")
                correct = row.get("correct_results_count", 0)
                total = row.get("total_results_count", 0)
                pct = (correct / total) * 100 if total > 0 else 0
                if name == "exact_match":
                    details["exact_match"] = f"{pct:.0f}%"
                elif name == "llmrater":
                    details["llmrater"] = f"{pct:.0f}%"
                elif name == "trajectory_matcher":
                    details["trajectory_matcher"] = f"{pct:.0f}%"
                elif name == "turn_count":
                    details["turn_count"] = f"{correct:.1f}"
                elif name == "executable":
                    details["executable"] = f"{pct:.0f}%"
                elif name == "token_consumption":
                    details["token_consumption"] = f"{correct:.0f}"
                elif name == "end_to_end_latency":
                    details["end_to_end_latency"] = f"{correct / 60000.0:.2f}m"
        except Exception as e:
            logging.warning(f"Error reading summary.csv for {dir_name}: {e}")

    return details


def get_color_for_pct(val_str):
    if not val_str or not val_str.endswith("%"):
        return "#334155"  # Default color
    try:
        val = float(val_str.rstrip("%"))
        if val >= 80:
            return "#16a34a"  # Green
        elif val >= 40:
            return "#ca8a04"  # Yellow
        else:
            return "#dc2626"  # Red
    except Exception:
        return "#334155"


def on_load(e: me.LoadEvent):
    state = me.state(State)
    results_dir = get_results_dir()
    directories = []
    if os.path.exists(results_dir):
        # List directories only
        directories = [
            d
            for d in os.listdir(results_dir)
            if os.path.isdir(os.path.join(results_dir, d))
        ]

    job_id = me.query_params.get("job_id") or me.query_params.get("jobid")
    if job_id and job_id in directories:
        state.selected_directory = job_id

    tab = me.query_params.get("tab")
    eval1 = me.query_params.get("eval1")
    eval2 = me.query_params.get("eval2")

    if tab == "compare" and eval1 and eval2:
        state.selected_main_tab = "Compare"
        state.compare_tab_visible = True
        state.compare_evals = json.dumps([eval1, eval2])
        # Trigger the AI comparison
        state.ai_comparison = compare_evals(eval1, eval2)





def status_component():
    results_dir = get_results_dir()
    directories = []
    if os.path.exists(results_dir):
        directories = [
            d
            for d in os.listdir(results_dir)
            if os.path.isdir(os.path.join(results_dir, d))
        ]
    
    with me.box(
        style=me.Style(
            background="#ffffff",
            padding=me.Padding.all("24px"),
            border_radius="12px",
            border=me.Border.all(
                me.BorderSide(width="1px", style="solid", color="#e0e0e0")
            ),
            box_shadow="0 2px 4px rgba(0,0,0,0.05)",
        )
    ):
        me.text("Product status", type="headline-5")
        me.box(style=me.Style(height="16px"))
        me.text(f"Total Evaluation Jobs: {len(directories)}")
        
        me.box(style=me.Style(height="24px"))
        me.text("Product Performance (Latest Eval per Product)", type="headline-6")
        me.box(style=me.Style(height="8px"))
        
        # Add agent tabs
        state = me.state(State)
        def on_agent_tab_change(e):
            st = me.state(State)
            st.status_agent_tab = e.value
            
        me.button_toggle(
            value=state.status_agent_tab,
            buttons=[
                me.ButtonToggleButton(label="Gemini", value="Gemini"),
                me.ButtonToggleButton(label="Claude", value="Claude"),
            ],
            on_change=on_agent_tab_change,
        )
        me.box(style=me.Style(height="16px"))
        
        # Build summary data from precomputed trends cache
        data = []
        cache_file = os.path.join(results_dir, "trends_cache.csv")
        if os.path.exists(cache_file):
            try:
                cache_df = pd.read_csv(cache_file)
                for _, row in cache_df.iterrows():
                    data.append({
                        'AI Score': row['ai_score'] if 'ai_score' in row else None,
                        'Product': row['product'],
                        'Dataset': row['dataset'] if 'dataset' in row and not pd.isna(row['dataset']) else "N/A",
                        'model_config.generator': row['model_config.generator'] if 'model_config.generator' in row else "unknown",
                        'Trajectory Matcher': row['trajectory'],
                        'Goal Completion': row['goal_completion'] if 'goal_completion' in row else None,
                        'Turn Count': row['turn_count'],
                        'Executable': row['executable'],
                        'Token Consumption': row['tokens'],
                        'End-to-End Latency': row['latency'],
                        'Run Time': row['run_time']
                    })
            except Exception as e:
                logging.error(f"Error reading trends cache: {e}")
        else:
            logging.warning(f"Trends cache file not found at {cache_file}")
            me.text("Trends cache file not found. Please run precompute.")
            return
                    
        # Add requested default products if not present in data
        default_products = ['spanner', 'bigtable', 'alloydb', 'memorystore', 'dms', 'datastream']
        products_in_data = [d['Product'] for d in data]
        for p in default_products:
            if p not in products_in_data:
                data.append({
                    'Product': p,
                    'Dataset': 'N/A',
                    'model_config.generator': 'unknown',
                    'AI Score': None,
                    'Trajectory Matcher': None,
                    'Goal Completion': None,
                    'Turn Count': None,
                    'Executable': None,
                    'Token Consumption': None,
                    'End-to-End Latency': None,
                    'Run Time': None
                })
                    
        if data:
            df = pd.DataFrame(data)
            # Filter out unknown products
            df = df[df['Product'] != 'unknown']
            
            if not df.empty:
                # Sort by Run Time descending to get the latest
                df['Run Time'] = pd.to_datetime(df['Run Time'])
                df = df.sort_values('Run Time', ascending=False, na_position='last')
                
                # Group by Product and Dataset and take the first (latest)
                summary_df = df.groupby(["Product", "Dataset", "model_config.generator"], dropna=False).first().reset_index()
                
                # Filter by agent tab
                if state.status_agent_tab == "Gemini":
                    summary_df = summary_df[(summary_df['model_config.generator'] == 'gemini_cli') | (summary_df['model_config.generator'] == 'unknown') | (summary_df['model_config.generator'] == 'N/A') | summary_df['Product'].isin(default_products)]
                
                # Render table similar to lists tab
                with me.box(
                    style=me.Style(
                        display="table",
                        width="100%",
                        border=me.Border.all(
                            me.BorderSide(width="1px", color="#e5e7eb", style="solid")
                        ),
                        border_radius="8px",
                        background="#ffffff",
                        margin=me.Margin(top="16px"),
                    )
                ):
                    # Header row
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
                        headers = [
                            "Product",
                            "Dataset",
                            "AI Score",
                            "Trajectory Matcher",
                            "Goal Completion",
                            "Turn Count",
                            "Executable",
                            "Token Consumption",
                            "End-to-End Latency"
                        ]
                        for label in headers:
                            with me.box(
                                style=me.Style(
                                    display="table-cell",
                                    padding=me.Padding.symmetric(vertical="12px", horizontal="16px"),
                                    text_align="center",
                                    border=me.Border.all(
                                        me.BorderSide(width="1px", color="#e2e8f0", style="solid")
                                    ),
                                    background="#f8fafc",
                                )
                            ):
                                me.text(label)
                                
                    # Data rows
                    for idx, row in summary_df.iterrows():
                        is_na = pd.isna(row['Trajectory Matcher'])
                        
                        # Mask scores in Claude tab if not Claude or N/A generator
                        if state.status_agent_tab == "Claude":
                            gen = str(row.get('model_config.generator', '')).lower()
                            if not ('claude' in gen or gen == 'n/a'):
                                is_na = True
                        
                        with me.box(
                            style=me.Style(
                                display="table-row",
                                background="#ffffff" if idx % 2 == 0 else "#f9fafb",
                            )
                        ):
                            def render_cell(text, color="#334155", cell_bg=None, on_click=None):
                                style = me.Style(
                                    display="table-cell",
                                    padding=me.Padding.symmetric(vertical="12px", horizontal="16px"),
                                    text_align="center",
                                    border=me.Border.all(
                                        me.BorderSide(width="1px", color="#e2e8f0", style="solid")
                                    ),
                                )
                                if cell_bg:
                                    style.background = cell_bg
                                with me.box(style=style):
                                    if on_click:
                                        me.button(
                                            text,
                                            on_click=on_click,
                                            style=me.Style(
                                                color=color,
                                                background="transparent",
                                                border=me.Border.all(me.BorderSide(width="0px")),
                                                padding=me.Padding.all("0px"),
                                                margin=me.Margin.all("0px"),
                                                font_size="inherit",
                                                font_weight="500",
                                                cursor="pointer",
                                            )
                                        )
                                    else:
                                        me.text(text, style=me.Style(color=color))
                                    
                            product_val = str(row['Product'])
                            dataset_val = str(row['Dataset'])
                            
                            def make_click_handler(p_val, d_val, g_val):
                                def handler(e: me.ClickEvent):
                                    st = me.state(State)
                                    st.selected_main_tab = "List"
                                    st.product_filter = p_val
                                    st.dataset_filter = d_val
                                    st.list_agent_tab = st.status_agent_tab
                                
                                safe_p = str(p_val).replace(" ", "_").replace(".", "_").replace("-", "_")
                                safe_d = str(d_val).replace(" ", "_").replace(".", "_").replace("-", "_")
                                handler_name = f"click_status_row_{safe_p}_{safe_d}"
                                handler.__name__ = handler_name
                                globals()[handler_name] = handler
                                return handler
                                
                            click_handler = make_click_handler(product_val, dataset_val, row.get('model_config.generator'))
                            render_cell(product_val, color="#2563eb", on_click=click_handler)
                            render_cell("N/A" if is_na else dataset_val, color="#2563eb", on_click=None if is_na else click_handler)
                            
                            if is_na or 'AI Score' not in row or pd.isna(row['AI Score']):
                                render_cell("N/A", color="#94a3b8", cell_bg="#e2e8f0")
                            else:
                                score_str = f"{row['AI Score']:.0f}%"
                                render_cell(score_str, get_color_for_pct(score_str))
                            
                            if is_na:
                                # Make cells gray for products with no data
                                render_cell("N/A", color="#94a3b8", cell_bg="#e2e8f0")
                                render_cell("N/A", color="#94a3b8", cell_bg="#e2e8f0")
                                render_cell("N/A", color="#94a3b8", cell_bg="#e2e8f0")
                                render_cell("N/A", color="#94a3b8", cell_bg="#e2e8f0")
                                render_cell("N/A", color="#94a3b8", cell_bg="#e2e8f0")
                                render_cell("N/A", color="#94a3b8", cell_bg="#e2e8f0")
                            else:
                                traj_str = f"{row['Trajectory Matcher']:.0f}%"
                                render_cell(traj_str, get_color_for_pct(traj_str))
                                
                                if 'Goal Completion' not in row or pd.isna(row['Goal Completion']):
                                    render_cell("N/A", color="#94a3b8", cell_bg="#e2e8f0")
                                else:
                                    goal_str = f"{row['Goal Completion']:.0f}%"
                                    render_cell(goal_str, get_color_for_pct(goal_str))
                                
                                render_cell(f"{row['Turn Count']:.1f}")
                                
                                exec_str = f"{row['Executable']:.0f}%"
                                render_cell(exec_str, get_color_for_pct(exec_str))
                                
                                render_cell(f"{row['Token Consumption']:.0f}")
                                if pd.isna(row['End-to-End Latency']):
                                    render_cell("N/A")
                                else:
                                    render_cell(f"{row['End-to-End Latency'] / 60000.0:.2f}m")
            else:
                me.text("No evaluation data found for known products.")
        else:
            me.text("No evaluation data found in results directories.")


def list_view_component(directories, results_dir):
    state = me.state(State)
    try:
        selected_evals_list = json.loads(state.selected_evals)
    except Exception:
        selected_evals_list = []
    with me.box(
        style=me.Style(
            background="#ffffff",
            padding=me.Padding.all("12px"),
            border_radius="12px",
            border=me.Border.all(
                me.BorderSide(
                    width="1px",
                    color="#e5e7eb",
                    style="solid",
                )
            ),
            box_shadow="0 1px 3px rgba(0,0,0,0.06)",
            text_align="center",
            margin=me.Margin(top="16px"),
        )
    ):
        me.text(
            "Welcome to EvalBench Viewer",
            style=me.Style(
                font_size="24px",
                font_weight="700",
                color="#1f2937",
                margin=me.Margin(bottom="8px"),
            ),
        )
        me.text(
            f"Found {len(directories)} evaluation runs. "
            "Click on an Eval ID in the table below to explore "
            "the results.",
            style=me.Style(
                font_size="16px",
                color="#6b7280",
                margin=me.Margin(bottom="16px"),
            ),
        )
        
        # Add agent tabs for List view
        def on_list_agent_tab_change(e):
            st = me.state(State)
            st.list_agent_tab = e.value
            
        me.button_toggle(
            value=state.list_agent_tab,
            buttons=[
                me.ButtonToggleButton(label="Gemini", value="Gemini"),
                me.ButtonToggleButton(label="Claude", value="Claude"),
            ],
            on_change=on_list_agent_tab_change,
        )
        me.box(style=me.Style(height="16px"))
        if directories:
            # Compute summaries if empty
            s = me.state(State)
            summaries = []
            if s.eval_summaries:
                try:
                    summaries = json.loads(s.eval_summaries)
                except Exception:
                    summaries = []
    
            if not summaries:
                cache_file = os.path.join(results_dir, "trends_cache.csv")
                logging.info(f"trends_cache.csv exists: {os.path.exists(cache_file)}")
                if os.path.exists(cache_file):
                    try:
                        cache_df = pd.read_csv(cache_file)
                        logging.info(f"Loaded {len(cache_df)} rows from trends cache.")
                        for _, row in cache_df.iterrows():
                            score = row['ai_score'] if 'ai_score' in row else 0.0
                            if (score == 0.0 or pd.isna(score)) and 'ai_summary' in row and not pd.isna(row['ai_summary']):
                                import re
                                match = re.search(r"General Score:.*?(\d+(\.\d+)?)", row['ai_summary'])
                                if match:
                                    score = float(match.group(1))
                                    
                            summaries.append({
                                "id": str(row['job_id']),
                                "date": str(row['run_time']) if not pd.isna(row['run_time']) else "N/A",
                                "product": str(row['product']) if not pd.isna(row['product']) else "N/A",
                                "requester": str(row['requester']) if not pd.isna(row['requester']) else "N/A",
                                "dataset": str(row['dataset']) if 'dataset' in row and not pd.isna(row['dataset']) else "N/A",
                                "model_config.generator": str(row['model_config.generator']) if 'model_config.generator' in row and not pd.isna(row['model_config.generator']) else "unknown",
                                "ai_score": f"{score:.0f}%" if not pd.isna(score) and score != 0.0 else "N/A",
                                "exact_match": f"{row['exact_match']:.0f}%" if not pd.isna(row['exact_match']) else "N/A",
                                "llmrater": f"{row['llmrater']:.0f}%" if not pd.isna(row['llmrater']) else "N/A",
                                "trajectory_matcher": f"{row['trajectory']:.0f}%" if not pd.isna(row['trajectory']) else "N/A",
                                "goal_completion": f"{row['goal_completion']:.0f}%" if 'goal_completion' in row and not pd.isna(row['goal_completion']) else "N/A",
                                "turn_count": f"{row['turn_count']:.1f}" if not pd.isna(row['turn_count']) else "N/A",
                                "executable": f"{row['executable']:.0f}%" if not pd.isna(row['executable']) else "N/A",
                                "token_consumption": f"{row['tokens']:.0f}" if not pd.isna(row['tokens']) else "N/A",
                                "end_to_end_latency": f"{row['latency'] / 60000.0:.2f}m" if not pd.isna(row['latency']) else "N/A"
                            })
                        s.eval_summaries = json.dumps(summaries)
                    except Exception as e:
                        logging.error(f"Error reading trends cache: {e}")
                else:
                    logging.warning(f"Trends cache file not found at {cache_file}")
    
            # Sort by selected column
            reverse = state.sort_descending
            col = state.sort_column
    
            def get_sort_key(x):
                val = x.get(col, "N/A")
    
                # Handle numbers and percentages
                if col in [
                    "exact_match",
                    "llmrater",
                    "trajectory_matcher",
                    "executable",
                    "ai_score",
                ]:
                    if val == "N/A":
                        return -1.0 if reverse else 101.0
                    if val.endswith("%"):
                        try:
                            return float(val.rstrip("%"))
                        except ValueError:
                            return -1.0 if reverse else 101.0
                    return -1.0 if reverse else 101.0
    
                elif col in [
                    "turn_count",
                    "token_consumption",
                    "end_to_end_latency",
                ]:
                    if val == "N/A":
                        return -1.0 if reverse else 1e12
                    try:
                        return float(val)
                    except ValueError:
                        return -1.0 if reverse else 1e12
    
                # String columns (product, requester, id, date)
                if val == "N/A":
                    return "" if reverse else "\xff\xff\xff\xff"
                return str(val)
    
            summaries.sort(key=get_sort_key, reverse=reverse)
    
            # Extract unique values for filters from ALL summaries
            all_summaries = []
            if s.eval_summaries:
                try:
                    all_summaries = json.loads(s.eval_summaries)
                except Exception:
                    all_summaries = []
    
            filters_file = os.path.join(results_dir, "filters_cache.json")
            if os.path.exists(filters_file):
                try:
                    with open(filters_file, "r") as f:
                        filters_data = json.load(f)
                    products = filters_data.get("products", [])
                    requesters = filters_data.get("requesters", [])
                    eval_ids = filters_data.get("eval_ids", [])
                    datasets = filters_data.get("datasets", [])
                except Exception as e:
                    logging.error(f"Error reading filters cache: {e}")
                    products = []
                    requesters = []
                    eval_ids = []
                    datasets = []
            else:
                products = sorted(
                    list(
                        set(
                            x["product"]
                            for x in all_summaries
                            if x["product"] != "N/A"
                        )
                    )
                )
                requesters = sorted(
                    list(
                        set(
                            x.get("requester", "N/A")
                            for x in all_summaries
                            if x.get("requester", "N/A") != "N/A"
                        )
                    )
                )
                eval_ids = sorted([x["id"] for x in all_summaries])
                datasets = sorted(
                    list(
                        set(
                            x.get("dataset", "N/A")
                            for x in all_summaries
                            if x.get("dataset", "N/A") != "N/A"
                        )
                    )
                )
            
            # Fallback for datasets if empty from cache
            if not datasets and all_summaries:
                datasets = sorted(
                    list(
                        set(
                            x.get("dataset", "N/A")
                            for x in all_summaries
                            if x.get("dataset", "N/A") != "N/A"
                        )
                    )
                )
    
            # Apply filters
            if state.list_agent_tab == "Gemini":
                summaries = [x for x in summaries if x.get("model_config.generator") == "gemini_cli" or x.get("model_config.generator") == "unknown" or x.get("model_config.generator") == "N/A" or x.get("product") in ['spanner', 'bigtable', 'alloydb', 'memorystore', 'dms', 'datastream']]
            elif state.list_agent_tab == "Claude":
                summaries = [x for x in summaries if x.get("model_config.generator") == "claude_code" or (x.get("model_config.generator") == "unknown" and 'claude' in str(x.get("product")).lower())]
            logging.info(f"Number of summaries after tab filter: {len(summaries)}")

            if state.eval_id_filter:
                summaries = [
                    x
                    for x in summaries
                    if x["id"] == state.eval_id_filter
                ]
            if state.product_filter:
                summaries = [
                    x
                    for x in summaries
                    if x["product"] == state.product_filter
                ]
            if state.requester_filter:
                summaries = [
                    x
                    for x in summaries
                    if x.get("requester", "N/A")
                    == state.requester_filter
                ]
            if state.dataset_filter:
                summaries = [
                    x
                    for x in summaries
                    if x.get("dataset", "N/A")
                    == state.dataset_filter
                ]
            
            if state.base_product:
                summaries = [
                    x
                    for x in summaries
                    if x["product"] == state.base_product
                ]
            if state.base_dataset:
                summaries = [
                    x
                    for x in summaries
                    if x.get("dataset", "N/A") == state.base_dataset
                ]

            # Limit number of rows to show after filter/sort
            summaries = summaries[:state.rows_to_show]
    
            # Render filters UI
            with me.box(
                style=me.Style(
                    display="flex",
                    flex_direction="row",
                    gap="24px",
                    margin=me.Margin(top="16px", bottom="24px"),
                    padding=me.Padding.all("16px"),
                    background="#ffffff",
                    border_radius="12px",
                    box_shadow=(
                        "0 1px 3px 0 rgba(0, 0, 0, 0.1), "
                        "0 1px 2px -1px rgba(0, 0, 0, 0.1)"
                    ),
                    align_items="center",
                    border=me.Border.all(
                        me.BorderSide(
                            width="1px",
                            color="#e2e8f0",
                            style="solid",
                        )
                    ),
                )
            ):
                def toggle_eval_id_dropdown(e: me.ClickEvent):
                    st = me.state(State)
                    if st.open_dropdown == "eval_id":
                        st.open_dropdown = ""
                    else:
                        st.open_dropdown = "eval_id"
    
                def make_eval_id_handler(val):
                    def handler(e: me.ClickEvent):
                        st = me.state(State)
                        st.eval_id_filter = val
                        st.open_dropdown = ""
    
                    handler_name = f"click_eval_id_{val}"
                    handler.__name__ = handler_name
                    globals()[handler_name] = handler
                    return handler
    
                with me.box(
                    style=me.Style(
                        position="relative",
                        width="200px",
                    )
                ):
                    # The Box acting as Dropdown Trigger
                    with me.box(
                        style=me.Style(
                            background="#ffffff",
                            border=me.Border.all(
                                me.BorderSide(
                                    width="1px",
                                    color="#e2e8f0",
                                )
                            ),
                            border_radius="4px",
                            padding=me.Padding.all("8px"),
                            cursor="pointer",
                        ),
                        on_click=toggle_eval_id_dropdown,
                    ):
                        me.text(
                            state.eval_id_filter
                            if state.eval_id_filter
                            else "Select Eval ID",
                            style=me.Style(
                                color="#1f2937"
                            ),
                        )
    
                    # The Popup List
                    if state.open_dropdown == "eval_id":
                        with me.box(
                            style=me.Style(
                                position="absolute",
                                top="100%",
                                left="0",
                                z_index=10,
                                background="#ffffff",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                    )
                                ),
                                border_radius="4px",
                                width="100%",
                                max_height="200px",
                                overflow_y="auto",
                            )
                        ):
                            # All option
                            with me.box(
                                style=me.Style(
                                    padding=me.Padding.all("8px"),
                                    cursor="pointer",
                                ),
                                on_click=make_eval_id_handler(""),
                            ):
                                me.text(
                                    "All",
                                    style=me.Style(
                                        color="#1f2937"
                                    ),
                                )
    
                            for d in eval_ids:
                                with me.box(
                                    style=me.Style(
                                        padding=me.Padding.all("8px"),
                                        cursor="pointer",
                                    ),
                                    on_click=make_eval_id_handler(d),
                                ):
                                    me.text(
                                        d,
                                        style=me.Style(
                                            color="#1f2937"
                                        ),
                                    )
    
                # Product Filter with Floating Autocomplete
                def toggle_product_dropdown(e: me.ClickEvent):
                    st = me.state(State)
                    if st.open_dropdown == "product":
                        st.open_dropdown = ""
                    else:
                        st.open_dropdown = "product"
    
                def make_prod_dropdown_handler(val):
                    def handler(e: me.ClickEvent):
                        st = me.state(State)
                        st.product_filter = val
                        st.open_dropdown = ""
    
                    # Sanitize name for Mesop event routing
                    safe_val = str(val).replace(" ", "_").replace(".", "_").replace("-", "_")
                    handler_name = f"click_prod_dd_{safe_val}"
                    handler.__name__ = handler_name
                    globals()[handler_name] = handler
                    return handler
    
                mk_prod_dd = make_prod_dropdown_handler
    
                with me.box(
                    style=me.Style(
                        position="relative",
                        width="200px",
                    )
                ):
                    # The Box acting as Dropdown Trigger
                    with me.box(
                        style=me.Style(
                            background="#ffffff",
                            border=me.Border.all(
                                me.BorderSide(
                                    width="1px",
                                    color="#e2e8f0",
                                )
                            ),
                            border_radius="4px",
                            padding=me.Padding.all("8px"),
                            cursor="pointer",
                        ),
                        on_click=toggle_product_dropdown,
                    ):
                        me.text(
                            state.product_filter
                            if state.product_filter
                            else "Filter by Product",
                            style=me.Style(
                                color="#1f2937"
                            ),
                        )
    
                    # The Popup List
                    if state.open_dropdown == "product":
                        with me.box(
                            style=me.Style(
                                position="absolute",
                                top="100%",
                                left="0",
                                z_index=10,
                                background="#ffffff",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                    )
                                ),
                                border_radius="4px",
                                width="100%",
                                max_height="200px",
                                overflow_y="auto",
                            )
                        ):
                            # All option
                            with me.box(
                                style=me.Style(
                                    padding=me.Padding.all("8px"),
                                    cursor="pointer",
                                ),
                                on_click=mk_prod_dd(""),
                            ):
                                me.text(
                                    "All",
                                    style=me.Style(
                                        color="#1f2937"
                                    ),
                                )
    
                            for p in products:
                                with me.box(
                                    style=me.Style(
                                        padding=me.Padding.all("8px"),
                                        cursor="pointer",
                                    ),
                                    on_click=mk_prod_dd(p),
                                ):
                                    me.text(
                                        p,
                                        style=me.Style(
                                            color="#1f2937"
                                        ),
                                    )
    
                # Requester Filter with Floating Autocomplete
                def toggle_requester_dropdown(e: me.ClickEvent):
                    st = me.state(State)
                    if st.open_dropdown == "requester":
                        st.open_dropdown = ""
                    else:
                        st.open_dropdown = "requester"
    
                def make_req_dropdown_handler(val):
                    def handler(e: me.ClickEvent):
                        st = me.state(State)
                        st.requester_filter = val
                        st.open_dropdown = ""
    
                    # Sanitize name for Mesop event routing
                    safe_val = str(val).replace(" ", "_").replace(".", "_").replace("-", "_")
                    handler_name = f"click_req_dd_{safe_val}"
                    handler.__name__ = handler_name
                    globals()[handler_name] = handler
                    return handler
    
                mk_req_dd = make_req_dropdown_handler
    
                with me.box(
                    style=me.Style(
                        position="relative",
                        width="200px",
                    )
                ):
                    # The Box acting as Dropdown Trigger
                    with me.box(
                        style=me.Style(
                            background="#ffffff",
                            border=me.Border.all(
                                me.BorderSide(
                                    width="1px",
                                    color="#e2e8f0",
                                )
                            ),
                            border_radius="4px",
                            padding=me.Padding.all("8px"),
                            cursor="pointer",
                        ),
                        on_click=toggle_requester_dropdown,
                    ):
                        me.text(
                            state.requester_filter
                            if state.requester_filter
                            else "Filter by Requester",
                            style=me.Style(
                                color="#1f2937"
                            ),
                        )
    
                    # The Popup List
                    if state.open_dropdown == "requester":
                        with me.box(
                            style=me.Style(
                                position="absolute",
                                top="100%",
                                left="0",
                                z_index=10,
                                background="#ffffff",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                    )
                                ),
                                border_radius="4px",
                                width="100%",
                                max_height="200px",
                                overflow_y="auto",
                            )
                        ):
                            # All option
                            with me.box(
                                style=me.Style(
                                    padding=me.Padding.all("8px"),
                                    cursor="pointer",
                                ),
                                on_click=mk_req_dd(""),
                            ):
                                me.text(
                                    "All",
                                    style=me.Style(
                                        color="#1f2937"
                                    ),
                                )
    
                            for r in requesters:
                                with me.box(
                                    style=me.Style(
                                        padding=me.Padding.all("8px"),
                                        cursor="pointer",
                                    ),
                                    on_click=mk_req_dd(r),
                                ):
                                    me.text(
                                        r,
                                        style=me.Style(
                                            color="#1f2937"
                                        ),
                                    )
    
                # Dataset Filter with Floating Autocomplete
                def toggle_dataset_dropdown(e: me.ClickEvent):
                    st = me.state(State)
                    if st.open_dropdown == "dataset":
                        st.open_dropdown = ""
                    else:
                        st.open_dropdown = "dataset"
    
                def make_dataset_dropdown_handler(val):
                    def handler(e: me.ClickEvent):
                        st = me.state(State)
                        st.dataset_filter = val
                        st.open_dropdown = ""
    
                    # Sanitize name for Mesop event routing
                    safe_val = str(val).replace(" ", "_").replace(".", "_").replace("-", "_")
                    handler_name = f"click_dataset_dd_{safe_val}"
                    handler.__name__ = handler_name
                    globals()[handler_name] = handler
                    return handler
    
                mk_dataset_dd = make_dataset_dropdown_handler
    
                with me.box(
                    style=me.Style(
                        position="relative",
                        width="300px",
                    )
                ):
                    # The Box acting as Dropdown Trigger
                    with me.box(
                        style=me.Style(
                            background="#ffffff",
                            border=me.Border.all(
                                me.BorderSide(
                                    width="1px",
                                    color="#e2e8f0",
                                )
                            ),
                            border_radius="4px",
                            padding=me.Padding.all("8px"),
                            cursor="pointer",
                        ),
                        on_click=toggle_dataset_dropdown,
                    ):
                        me.text(
                            state.dataset_filter
                            if state.dataset_filter
                            else "Filter by Dataset",
                            style=me.Style(
                                color="#1f2937"
                            ),
                        )
    
                    # The Popup List
                    if state.open_dropdown == "dataset":
                        with me.box(
                            style=me.Style(
                                position="absolute",
                                top="100%",
                                left="0",
                                z_index=10,
                                background="#ffffff",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                    )
                                ),
                                border_radius="4px",
                                width="100%",
                                max_height="200px",
                                overflow_y="auto",
                            )
                        ):
                            # All option
                            with me.box(
                                style=me.Style(
                                    padding=me.Padding.all("8px"),
                                    cursor="pointer",
                                ),
                                on_click=mk_dataset_dd(""),
                            ):
                                me.text(
                                    "All",
                                    style=me.Style(
                                        color="#1f2937"
                                    ),
                                )
    
                            for d in datasets:
                                with me.box(
                                    style=me.Style(
                                        padding=me.Padding.all("8px"),
                                        cursor="pointer",
                                    ),
                                    on_click=mk_dataset_dd(d),
                                ):
                                    me.text(
                                        d,
                                        style=me.Style(
                                            color="#1f2937"
                                        ),
                                    )
                
                # Rows to Show Filter
                def toggle_rows_dropdown(e: me.ClickEvent):
                    st = me.state(State)
                    if st.open_dropdown == "rows_to_show":
                        st.open_dropdown = ""
                    else:
                        st.open_dropdown = "rows_to_show"
    
                def make_rows_handler(val):
                    def handler(e: me.ClickEvent):
                        st = me.state(State)
                        st.rows_to_show = val
                        st.open_dropdown = ""
    
                    handler_name = f"click_rows_{val}"
                    handler.__name__ = handler_name
                    globals()[handler_name] = handler
                    return handler
    
                with me.box(
                    style=me.Style(
                        position="relative",
                        width="120px",
                    )
                ):
                    # The Box acting as Dropdown Trigger
                    with me.box(
                        style=me.Style(
                            background="#ffffff",
                            border=me.Border.all(
                                me.BorderSide(
                                    width="1px",
                                    color="#e2e8f0",
                                )
                            ),
                            border_radius="4px",
                            padding=me.Padding.all("8px"),
                            cursor="pointer",
                        ),
                        on_click=toggle_rows_dropdown,
                    ):
                        me.text(
                            f"Show: {state.rows_to_show}",
                            style=me.Style(
                                color="#1f2937"
                            ),
                        )
    
                    # The Popup List
                    if state.open_dropdown == "rows_to_show":
                        with me.box(
                            style=me.Style(
                                position="absolute",
                                top="100%",
                                left="0",
                                z_index=10,
                                background="#ffffff",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                    )
                                ),
                                border_radius="4px",
                                width="100%",
                                max_height="200px",
                                overflow_y="auto",
                            )
                        ):
                            for opt in [5, 10, 20, 50, 100]:
                                with me.box(
                                    style=me.Style(
                                        padding=me.Padding.all("8px"),
                                        cursor="pointer",
                                    ),
                                    on_click=make_rows_handler(opt),
                                ):
                                    me.text(
                                        str(opt),
                                        style=me.Style(
                                            color="#1f2937"
                                        ),
                                    )
                
                def make_select_handler(job_id, item_product, item_dataset):
                    def handler(e: me.ClickEvent):
                        st = me.state(State)
                        try:
                            sel = json.loads(st.selected_evals)
                        except Exception:
                            sel = []
                        
                        if job_id in sel:
                            sel.remove(job_id)
                            if not sel:
                                st.base_product = ""
                                st.base_dataset = ""
                        else:
                            if len(sel) == 0:
                                sel.append(job_id)
                                st.base_product = item_product
                                st.base_dataset = item_dataset
                            elif len(sel) == 1:
                                sel.append(job_id)
                        
                        st.selected_evals = json.dumps(sel)
                    
                    safe_id = str(job_id).replace(" ", "_").replace(".", "_").replace("-", "_")
                    handler.__name__ = f"click_select_{safe_id}"
                    return handler

                def on_reset_click(e: me.ClickEvent):
                    st = me.state(State)
                    st.eval_id_filter = ""
                    st.product_filter = ""
                    st.requester_filter = ""
                    st.dataset_filter = ""
                    st.open_dropdown = ""
                    st.selected_evals = "[]"
                    st.base_product = ""
                    st.base_dataset = ""
                    st.select_mode_active = False
                    st.compare_tab_visible = False
                    st.ai_comparison = ""
                
                me.button(
                    "Reset",
                    on_click=on_reset_click,
                    style=me.Style(
                        background="#ef4444",
                        color="#ffffff",
                        font_weight="600",
                        padding=me.Padding.symmetric(vertical="8px", horizontal="16px"),
                        border_radius="4px",
                        cursor="pointer",
                    )
                )
                
                def on_toggle_select_mode(e: me.ClickEvent):
                    st = me.state(State)
                    st.select_mode_active = not st.select_mode_active
                
                me.button(
                    "Compare",
                    on_click=on_toggle_select_mode,
                    style=me.Style(
                        background="#0284c7" if state.select_mode_active else "#e2e8f0",
                        color="#ffffff" if state.select_mode_active else "#475569",
                        font_weight="600",
                        padding=me.Padding.symmetric(vertical="8px", horizontal="16px"),
                        border_radius="4px",
                        cursor="pointer",
                        margin=me.Margin(left="8px"),
                    )
                )
    
            def on_sort_click(col_name):
                s = me.state(State)
                if s.sort_column == col_name:
                    s.sort_descending = not s.sort_descending
                else:
                    s.sort_column = col_name
                    s.sort_descending = True
    
            def click_id(e):
                on_sort_click("id")
    
            def click_date(e):
                on_sort_click("date")
    
            def click_product(e):
                on_sort_click("product")
    
            def click_requester(e):
                on_sort_click("requester")
    
            def click_traj(e):
                on_sort_click("trajectory_matcher")
    
            def click_turns(e):
                on_sort_click("turn_count")
    
            def click_exec(e):
                on_sort_click("executable")
    
            def click_dataset(e):
                on_sort_click("dataset")

            def click_tokens(e):
                on_sort_click("token_consumption")
    
            def click_latency(e):
                on_sort_click("end_to_end_latency")
    
            def click_goal_comp(e):
                on_sort_click("goal_completion")
    
            def click_ai_score(e):
                on_sort_click("ai_score")

            def click_select(e):
                pass

            sort_handlers = {
                "select": click_select,
                "id": click_id,
                "date": click_date,
                "product": click_product,
                "requester": click_requester,
                "dataset": click_dataset,
                "ai_score": click_ai_score,
                "trajectory_matcher": click_traj,
                "goal_completion": click_goal_comp,
                "turn_count": click_turns,
                "executable": click_exec,
                "token_consumption": click_tokens,
                "end_to_end_latency": click_latency,
            }
    
            def render_header_cell(h_label, h_col, h_width):
                with me.box(
                    style=me.Style(
                        display="table-cell",
                        padding=me.Padding.symmetric(
                            vertical="12px", horizontal="16px"
                        ),
                        text_align="center",
                        border=me.Border.all(
                            me.BorderSide(
                                width="1px",
                                color="#e2e8f0",
                                style="solid",
                            )
                        ),
                        cursor="pointer",
                        width=h_width,
                        white_space="normal",
                        background="#f8fafc",
                    ),
                    on_click=sort_handlers[h_col],
                ):
                    s = me.state(State)
                    arrow = " ↓" if s.sort_descending else " ↑"
                    arrow_str = arrow if s.sort_column == h_col else ""
                    
                    words = h_label.split(" ")
                    with me.box(
                        style=me.Style(
                            display="flex",
                            flex_direction="column",
                            align_items="center",
                            justify_content="center",
                            color="#475569",
                        )
                    ):
                        for i, w in enumerate(words):
                            if i == len(words) - 1:
                                with me.box(style=me.Style(display="flex", flex_direction="row", align_items="center")):
                                    me.text(w)
                                    if arrow_str:
                                        me.text(
                                            arrow_str,
                                            style=me.Style(
                                                font_weight="bold",
                                                color="#0284c7",
                                                font_size="14px",
                                                margin=me.Margin(left="4px"),
                                            ),
                                        )
                            else:
                                me.text(w)
    
            # Selection Toolbar
            if selected_evals_list:
                with me.box(
                    style=me.Style(
                        display="flex",
                        flex_direction="row",
                        justify_content="space-between",
                        align_items="center",
                        padding=me.Padding.all("8px"),
                        background="#e0f2fe",
                        border_radius="4px",
                        margin=me.Margin(top="16px"),
                    )
                ):
                    me.text(f"Selected: {len(selected_evals_list)} / 2", style=me.Style(color="#0369a1", font_weight="600"))
                    
                    def on_clear_selection(e: me.ClickEvent):
                        st = me.state(State)
                        st.selected_evals = "[]"
                        st.base_product = ""
                        st.base_dataset = ""
                    
                    def on_compare_click(e: me.ClickEvent):
                        st = me.state(State)
                        st.compare_tab_visible = True
                        st.compare_evals = st.selected_evals
                        st.selected_main_tab = "Compare"
                        
                        if not st.ai_comparison:
                            st.ai_comparison = "Comparing..."
                            logging.info("Set ai_comparison to Comparing... in on_compare_click")
                            yield
                            
                            try:
                                comp_evals = json.loads(st.compare_evals)
                            except Exception:
                                comp_evals = []
                                
                            logging.info(f"comp_evals in on_compare_click: {comp_evals}")
                            if len(comp_evals) == 2:
                                logging.info("Starting compare_evals in on_compare_click...")
                                st.ai_comparison = compare_evals(comp_evals[0], comp_evals[1])
                                logging.info("Finished compare_evals in on_compare_click.")
                                yield
                    
                    with me.box(style=me.Style(display="flex", gap="8px")):
                        me.button(
                            "Clear",
                            on_click=on_clear_selection,
                            style=me.Style(
                                background="#ef4444",
                                color="#ffffff",
                                font_weight="600",
                            )
                        )
                        if len(selected_evals_list) == 2 and me.state(State).selected_main_tab != "Compare":
                            me.button(
                                "Compare",
                                on_click=on_compare_click,
                                style=me.Style(
                                    background="#10b981",
                                    color="#ffffff",
                                    font_weight="600",
                                ),
                            )

            with me.box(
                style=me.Style(
                    max_height="600px",
                    overflow_y="auto",
                    margin=me.Margin(top="16px"),
                    display="table",
                    width="100%",
                    border=me.Border.all(
                        me.BorderSide(
                            width="1px",
                            color="#e5e7eb",
                            style="solid",
                        )
                    ),
                    border_radius="8px",
                    background="#ffffff",
                )
            ):
                # Header row
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
                    headers = []
                    if state.select_mode_active:
                        headers.append(("Select", "select", "8ch"))
                    headers.extend([
                        ("Eval ID", "id", "36ch"),
                        ("Date", "date", "20ch"),
                        ("Product", "product", "12ch"),
                        ("Requester", "requester", "12ch"),
                        ("Dataset", "dataset", "15ch"),
                        ("AI Score", "ai_score", "8ch"),
                        ("Trajectory Matcher", "trajectory_matcher", "12ch"),
                        ("Goal Completion", "goal_completion", "12ch"),
                        ("Turn Count", "turn_count", "8ch"),
                        ("Executable", "executable", "10ch"),
                        ("Token Consumption", "token_consumption", "12ch"),
                        ("End-to-End Latency", "end_to_end_latency", "12ch"),
                    ])
                    for label, col, width in headers:
                        render_header_cell(label, col, width)
    
                # Data rows
                for idx, item in enumerate(summaries):
                    d = item["id"]
                    date_val = item.get("date", "N/A")
                    prod = item["product"]
                    req_val = item.get("requester", "N/A")
                    dataset_val = item.get("dataset", "N/A")
                    ai_score_val = item.get("ai_score", "N/A")
                    traj = item.get("trajectory_matcher", "N/A")
                    goal_comp = item.get("goal_completion", "N/A")
                    turns = item.get("turn_count", "N/A")
                    exec_val = item.get("executable", "N/A")
                    tokens = item.get("token_consumption", "N/A")
                    latency = item.get("end_to_end_latency", "N/A")
    
                    bg_color = (
                        "#ffffff"
                        if idx % 2 == 0
                        else "#f8fafc"
                    )
    

    
                    with me.box(
                        style=me.Style(
                            display="table-row",
                            background=bg_color,
                        )
                    ):
                        # Select checkbox
                        if state.select_mode_active:
                            with me.box(
                                style=me.Style(
                                    display="table-cell",
                                    padding=me.Padding.symmetric(
                                        vertical="10px", horizontal="16px"
                                    ),
                                    text_align="center",
                                    border=me.Border.all(
                                        me.BorderSide(
                                            width="1px",
                                            color="#e2e8f0",
                                            style="solid",
                                        )
                                    ),
                                    width="8ch",
                                )
                            ):
                                is_selected = d in selected_evals_list
                                label = "✅" if is_selected else "⬜"
                                me.button(
                                    label,
                                    on_click=make_select_handler(d, prod, dataset_val),
                                    style=me.Style(
                                        background="transparent",
                                        color="#0284c7" if is_selected else "#64748b",
                                        font_weight="bold",
                                        border=me.Border.all(me.BorderSide(width="0px")),
                                        cursor="pointer",
                                    ),
                                )
                        # Eval ID as a link/button
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                                width="36ch",
                                white_space="nowrap",
                            )
                        ):
                            with me.box(
                                style=me.Style(
                                    display="flex",
                                    justify_content="center",
                                    width="100%",
                                )
                            ):
                                me.markdown(f'<a href="/?job_id={d}" class="pill-link">{d}</a>')
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                                width="24ch",
                                white_space="nowrap",
                            )
                        ):
                            me.text(
                                date_val,
                                style=me.Style(
                                    color="#334155",
                                    font_family="monospace",
                                ),
                            )
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                            )
                        ):
                            me.text(
                                prod,
                                style=me.Style(
                                    color="#334155"
                                ),
                            )
    
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                            )
                        ):
                            me.text(
                                req_val,
                                style=me.Style(
                                    color="#334155"
                                ),
                            )
                        
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                            )
                        ):
                            me.text(
                                dataset_val,
                                style=me.Style(
                                    color="#334155"
                                ),
                            )
                        
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                                width="10ch",
                                white_space="nowrap",
                            )
                        ):
                            me.text(
                                ai_score_val,
                                style=me.Style(
                                    color=get_color_for_pct(ai_score_val)
                                ),
                            )
    
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                                width="18ch",
                                white_space="nowrap",
                            )
                        ):
                            me.text(
                                traj,
                                style=me.Style(
                                    color=get_color_for_pct(traj)
                                ),
                            )
    
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                                width="16ch",
                                white_space="nowrap",
                            )
                        ):
                            me.text(
                                goal_comp,
                                style=me.Style(
                                    color=get_color_for_pct(goal_comp)
                                ),
                            )
    
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                            )
                        ):
                            me.text(
                                turns,
                                style=me.Style(
                                    color="#334155"
                                ),
                            )
    
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                            )
                        ):
                            me.text(
                                exec_val,
                                style=me.Style(
                                    color=get_color_for_pct(exec_val)
                                ),
                            )
    
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                            )
                        ):
                            me.text(
                                tokens,
                                style=me.Style(
                                    color="#334155"
                                ),
                            )
    
                        with me.box(
                            style=me.Style(
                                display="table-cell",
                                padding=me.Padding.symmetric(
                                    vertical="10px", horizontal="16px"
                                ),
                                text_align="center",
                                border=me.Border.all(
                                    me.BorderSide(
                                        width="1px",
                                        color="#e2e8f0",
                                        style="solid",
                                    )
                                ),
                            )
                        ):
                            me.text(
                                latency,
                                style=me.Style(
                                    color="#334155"
                                ),
                            )
    
    


@me.page(
    path="/",
    title="EvalBench Viewer",
    on_load=on_load,
    security_policy=me.SecurityPolicy(
        dangerously_disable_trusted_types=True,
        cross_origin_opener_policy="same-origin",
    ),
    stylesheets=["/static/style.css"],
)
def app():
    with me.box(
        style=me.Style(
            background="#f8fafc",
            min_height="100vh",
            width="100%",
        )
    ):
        render_app_content()


def on_status_tab_click(e: me.ClickEvent):
    st = me.state(State)
    st.selected_main_tab = "Status"
    logging.info("Tab clicked: Status")

def on_list_tab_click(e: me.ClickEvent):
    st = me.state(State)
    st.selected_main_tab = "List"
    logging.info("Tab clicked: List")

def on_charts_tab_click(e: me.ClickEvent):
    st = me.state(State)
    st.selected_main_tab = "Charts"
    logging.info("Tab clicked: Charts")

def on_compare_tab_click(e: me.ClickEvent):
    st = me.state(State)
    st.selected_main_tab = "Compare"
    logging.info("Tab clicked: Compare")
    if not st.ai_comparison:
        st.ai_comparison = "Comparing..."
        logging.info("Set ai_comparison to Comparing...")
        yield
        
        try:
            comp_evals = json.loads(st.compare_evals)
        except Exception:
            comp_evals = []
            
        logging.info(f"comp_evals: {comp_evals}")
        if len(comp_evals) == 2:
            logging.info("Starting compare_evals...")
            st.ai_comparison = compare_evals(comp_evals[0], comp_evals[1])
            logging.info("Finished compare_evals.")
            yield


def render_app_content():
    try:
        state = me.state(State)
        results_dir = get_results_dir()
        logging.info(f"render_app_content: selected_directory='{state.selected_directory}', selected_evals='{state.selected_evals}', selected_main_tab='{state.selected_main_tab}'")
    
        directories = []
        if os.path.exists(results_dir):
            # List directories only
            directories = [
                d
                for d in os.listdir(results_dir)
                if os.path.isdir(os.path.join(results_dir, d))
            ]
    
        def on_title_click(e: me.ClickEvent):
            state.selected_directory = ""
            state.conversation_index = 0
            me.navigate("/")
            
        def on_clear_cache_click(e: me.ClickEvent):
            results_dir = get_results_dir()
            processed_dirs_file = os.path.join(results_dir, "processed_dirs.json")
            trends_cache_file = os.path.join(results_dir, "trends_cache.csv")
            filters_cache_file = os.path.join(results_dir, "filters_cache.json")
            
            try:
                if os.path.exists(processed_dirs_file):
                    os.remove(processed_dirs_file)
                if os.path.exists(trends_cache_file):
                    os.remove(trends_cache_file)
                if os.path.exists(filters_cache_file):
                    os.remove(filters_cache_file)
                
                logging.info("Cleared precomputed files. Triggering precompute...")
                
                import threading
                threading.Thread(target=precompute_trends.precompute).start()
                
                state.cache_cleared_message = "Cache cleared. Precompute triggered in background."
            except Exception as ex:
                logging.error(f"Error clearing cache: {ex}")
                state.cache_cleared_message = f"Error clearing cache: {ex}"
    
        # Full-width header bar
        with me.box(
            style=me.Style(
                background="#1e293b",
                padding=me.Padding.symmetric(vertical="8px", horizontal="5%"),
                margin=me.Margin(bottom="24px"),
                display="flex",
                justify_content="space-between",
                align_items="center",
            )
        ):
            me.button(
                "EvalBench Viewer",
                on_click=on_title_click,
                style=me.Style(
                    color="#f8fafc",
                    font_size="22px",
                    font_weight="700",
                    letter_spacing="0.5px",
                    background="transparent",
                    padding=me.Padding.all("0px"),
                    margin=me.Margin.all("0px"),
                    border=me.Border.all(me.BorderSide(width="0px")),
                    text_align="left",
                ),
            )
    
            import time
            cache_file = os.path.join(results_dir, "trends_cache.csv")
            cache_status = "Not Ready"
            cache_color = "#ef4444" # Red
            
            if os.path.exists(cache_file):
                mtime = os.path.getmtime(cache_file)
                elapsed = time.time() - mtime
                if elapsed < 600: # 10 minutes
                    cache_status = "Fresh"
                    cache_color = "#10b981" # Green
                else:
                    cache_status = "Stale"
                    cache_color = "#f59e0b" # Yellow
                    
            with me.box(
                style=me.Style(
                    display="flex",
                    flex_direction="column",
                    align_items="flex-end",
                    gap="4px",
                )
            ):
                if GIT_VERSION != "unknown":
                    with me.box(
                        style=me.Style(
                            font_size="12px",
                            color="#94a3b8",
                        )
                    ):
                        me.markdown(
                            f"[Git: {GIT_VERSION}](https://github.com/GoogleCloudPlatform/evalbench/commit/{GIT_VERSION})"
                        )
                
                with me.box(style=me.Style(display="flex", align_items="center", gap="6px", font_size="12px")):
                    me.box(style=me.Style(width="8px", height="8px", border_radius="50%", background=cache_color))
                    me.text(f"Cache: {cache_status}", style=me.Style(font_weight="500", color="#94a3b8"))
                    me.button(
                        "Clear Cache",
                        on_click=on_clear_cache_click,
                        style=me.Style(
                            color="transparent",
                            font_size="10px",
                            background="transparent",
                            padding=me.Padding.symmetric(vertical="2px", horizontal="4px"),
                            border_radius="3px",
                            margin=me.Margin(left="10px"),
                        )
                    )
    
        # Centered content at 90% browser width
        with me.box(
            style=me.Style(
                width="90%",
                margin=me.Margin.symmetric(horizontal="auto"),
                display="flex",
                flex_direction="column",
                gap="16px",
                background="#ffffff",
                padding=me.Padding.all("24px"),
                border_radius="8px",
                box_shadow="0 4px 6px -1px rgba(0, 0, 0, 0.1)",
                color="#1e293b",
            )
        ):

    
            if state.selected_directory:
    
                def on_tab_change(e: me.ButtonToggleChangeEvent):
                    state.selected_tab = e.value
                    
                def on_generate_summary_click(e: me.ClickEvent):
                    state = me.state(State)
                    results_dir_full = os.path.join(results_dir, state.selected_directory)
                    state.ai_summary = summarize_eval_scoring(results_dir_full)
                    # Parse score
                    import re
                    match = re.search(r"\*\*General Score:\s*(\d+(\.\d+)?)[^*]*\*\*", state.ai_summary)
                    if match:
                        state.ai_score = float(match.group(1))
                        
                def on_info_click(e: me.ClickEvent):
                    state = me.state(State)
                    state.show_formula = not state.show_formula
    
                me.button_toggle(
                    value=state.selected_tab,
                    buttons=[
                        me.ButtonToggleButton(
                            label="Dashboard", value="Dashboard"
                        ),
                        me.ButtonToggleButton(
                            label="Configs", value="Configs"
                        ),
                        # me.ButtonToggleButton(label="Evals", value="Evals"),
                        # me.ButtonToggleButton(label="Scores", value="Scores"),
                        me.ButtonToggleButton(
                            label="Conversations", value="Conversations"
                        ),
                    ],
                    on_change=on_tab_change,
                )

                # Read configs first to get interesting attributes
                config_path = os.path.join(
                    results_dir, state.selected_directory, "configs.csv"
                )
                interesting_configs = {}
                if os.path.exists(config_path):
                    try:
                        config_df = pd.read_csv(config_path)
                        
                        def get_val(cfg_name):
                            row = config_df[config_df['config'] == cfg_name]
                            if not row.empty:
                                return row['value'].values[0]
                            return None
                            
                        product = get_val('experiment_config.product_name')
                        requester = get_val('experiment_config.experiment_config.guitar_requester')
                        cli_version = get_val('model_config.gemini_cli_version')
                        orchestrator = get_val('experiment_config.orchestrator')
                        eval_group = get_val('experiment_config.eval_group')
                        
                        if product: interesting_configs['Product'] = product
                        if requester: interesting_configs['Requester'] = requester
                        if cli_version: interesting_configs['CLI Version'] = cli_version
                        if orchestrator: interesting_configs['Orchestrator'] = orchestrator
                        if eval_group: interesting_configs['Eval Group'] = eval_group
                        
                    except Exception as e:
                        logging.warning(f"Error reading configs for summary: {e}")

                if interesting_configs:
                    with me.box(style=me.Style(display="flex", flex_wrap="wrap", gap="24px", padding=me.Padding.all("16px"), background="#f8fafc", border_radius="8px", border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0")), margin=me.Margin(bottom="16px"))):
                        for k, v in interesting_configs.items():
                            with me.box(style=me.Style(display="flex", flex_direction="column", gap="4px")):
                                me.text(k, style=me.Style(font_size="16px", color="#64748b", font_weight="600", text_transform="uppercase"))
                                me.text(str(v), style=me.Style(font_size="20px", color="#0f172a", font_weight="500"))


    
                if state.selected_tab == "Dashboard":
                    dashboard.dashboard_component(
                        os.path.join(results_dir, state.selected_directory)
                    )
                    
                    me.divider()
                    me.text("AI Summary", type="headline-5")
                    
                    if not state.ai_summary and state.selected_directory:
                        trends_cache_file = os.path.join(results_dir, "trends_cache.csv")
                        if os.path.exists(trends_cache_file):
                            try:
                                cache_df = pd.read_csv(trends_cache_file)
                                run_data = cache_df[cache_df['job_id'] == state.selected_directory]
                                if not run_data.empty and 'ai_summary' in run_data.columns:
                                    summary = run_data['ai_summary'].values[0]
                                    if not pd.isna(summary) and summary != "N/A":
                                        state.ai_summary = summary
                                if not run_data.empty and 'ai_score' in run_data.columns:
                                    score = run_data['ai_score'].values[0]
                                    if not pd.isna(score):
                                        state.ai_score = float(score)
                                
                                # Fallback if score was not parsed correctly in cache
                                if state.ai_score == 0.0 and state.ai_summary:
                                    import re
                                    match = re.search(r"General Score:.*?(\d+(\.\d+)?)", state.ai_summary)
                                    if match:
                                        state.ai_score = float(match.group(1))
                            except Exception as e:
                                logging.error(f"Error reading AI summary from cache: {e}")
                    
                    if not state.ai_summary:
                        me.button("Generate AI Summary", on_click=on_generate_summary_click)
                    
                    if state.ai_summary:
                        with me.box(style=me.Style(display="flex", align_items="flex-start", margin=me.Margin(bottom="8px"))):
                            me.text(f"General Score: {state.ai_score}", type="headline-6")
                            with me.box(style=me.Style(margin=me.Margin(left="2px"), cursor="pointer"), on_click=on_info_click):
                                me.text("ⓘ", style=me.Style(color="#2563eb", font_size="12px"))
                        
                        if state.show_formula:
                            me.text("Formula: 0.4 * goal_completion + 0.2 * trajectory_matcher + 0.2 * behavioral_metrics + 0.2 * parameter_analysis", style=me.Style(font_size="14px", color="#6b7280", margin=me.Margin(bottom="16px")))
                        
                        # Strip score from summary if present to avoid duplication
                        import re
                        clean_summary = re.sub(r"^\s*\*\*General Score:\s*\d+(\.\d+)?[^*]*\*\*\s*", "", state.ai_summary)
                        me.markdown(clean_summary)
                        
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
                        me.text(
                            f"configs.csv not found in {state.selected_directory}"
                        )
                elif state.selected_tab == "Evals":
                    evals_path = os.path.join(
                        results_dir, state.selected_directory, "evals.csv"
                    )
                    if os.path.exists(evals_path):
                        try:
                            df = pd.read_csv(evals_path)
                            details = get_eval_details(
                                results_dir, state.selected_directory
                            )
                            df.insert(0, "orchestrator", details["orchestrator"])
                            me.table(data_frame=df)
                        except Exception as e:
                            me.text(f"Error reading evals.csv: {e}")
                    else:
                        me.text(
                            f"evals.csv not found in {state.selected_directory}"
                        )
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
                        me.text(
                            f"scores.csv not found in {state.selected_directory}"
                        )


            else:
                            from trends import trends_component
                            state = me.state(State)
                
                            def on_main_tab_change(e: me.ButtonToggleChangeEvent):
                                st = me.state(State)
                                st.selected_main_tab = e.value
                                logging.info(f"Main tab changed to: {e.value}")
                                
                            with me.box(style=me.Style(margin=me.Margin(bottom="12px"))):
                                buttons = [
                                    me.ButtonToggleButton(label="Status", value="Status"),
                                    me.ButtonToggleButton(label="List", value="List"),
                                    me.ButtonToggleButton(label="Charts", value="Charts"),
                                ]
                                if state.compare_tab_visible:
                                    buttons.append(me.ButtonToggleButton(label="Compare", value="Compare"))
                                    
                                me.button_toggle(
                                    value=state.selected_main_tab,
                                    buttons=buttons,
                                    on_change=on_main_tab_change,
                                )
                
                            if state.selected_main_tab == "List":
                                try:
                                    list_view_component(directories, results_dir)
                                except Exception as e:
                                    logging.exception("Failed to call list_view_component")
                                    me.text(f"Error: {e}")
                            elif state.selected_main_tab == "Charts":
                                trends_component()
                            elif state.selected_main_tab == "Status":
                                status_component()
                            elif state.selected_main_tab == "Compare":
                                try:
                                    comp_evals = json.loads(state.compare_evals)
                                except Exception:
                                    comp_evals = []
                                
                                with me.box(style=me.Style(padding=me.Padding.all("16px"))):
                                    me.text("Comparison", type="headline-5")
                                    if len(comp_evals) == 2:
                                        with me.box(style=me.Style(margin=me.Margin(bottom="16px"))):
                                            me.markdown(f'Comparing: <a href="/?job_id={comp_evals[0]}" target="_blank">{comp_evals[0]}</a> vs <a href="/?job_id={comp_evals[1]}" target="_blank">{comp_evals[1]}</a>')
                                        
                                        if not state.ai_comparison or state.ai_comparison == "Comparing...":
                                            me.text("Comparing...", style=me.Style(font_weight="bold", color="#0284c7", margin=me.Margin(bottom="16px")))
                                        else:
                                            with me.box(style=me.Style(
                                                background="#ffffff",
                                                padding=me.Padding.all("16px"),
                                                border_radius="8px",
                                                border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0")),
                                                margin=me.Margin(top="16px")
                                            )):
                                                me.markdown(state.ai_comparison)
                                    else:
                                        me.text("Invalid comparison state.")

                
    except Exception as e:
        logging.exception("render_app_content failed")
        me.text(f"Fatal Error: {e}")
if __name__ == "__main__":
    me.run(app)
