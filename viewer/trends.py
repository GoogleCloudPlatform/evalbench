import os
import logging
import mesop as me
import pandas as pd
from main import State

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

def generate_d3_chart(df, x_col, y_col, hue_col, title, ylabel):
    df_sorted = df.sort_values(by=x_col)
    
    # Convert dataframe to records for JSON
    data_records = df_sorted.to_dict(orient='records')
    import json
    data_json = json.dumps(data_records)
    
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <script src="https://d3js.org/d3.v7.min.js"></script>
        <style>
            body {{ font-family: Inter, system-ui, -apple-system, sans-serif; margin: 0; padding: 20px; background: transparent; }}
            .axis-label {{ font-size: 10px; fill: #64748b; }}
            .grid line {{ stroke: #e2e8f0; stroke-opacity: 0.7; }}
            .line {{ fill: none; stroke-width: 2px; }}
            .area {{ opacity: 0.1; pointer-events: none; }}
            .dot {{ stroke: #ffffff; stroke-width: 2px; cursor: pointer; }}
            .chart-title {{ font-size: 16px; font-weight: 700; fill: #1f2937; }}
            .tooltip {{
                position: absolute;
                background: rgba(15, 23, 42, 0.9);
                color: white;
                padding: 8px 12px;
                border-radius: 6px;
                font-size: 12px;
                pointer-events: none;
                opacity: 0;
                transition: opacity 0.2s;
                box-shadow: 0 4px 6px -1px rgb(0 0 0 / 0.1);
                z-index: 1000;
            }}
            .legend {{ font-size: 12px; fill: #475569; font-weight: 500; }}
        </style>
    </head>
    <body>
        <div id="chart-container" style="width: 100%; height: 500px; position: relative;">
            <div id="chart"></div>
            <div class="tooltip" id="tooltip"></div>
        </div>
        
        <script>
            window.chartData = {data_json};
            window.chartConfig = {{
                xCol: "{x_col}",
                yCol: "{y_col}",
                hueCol: "{hue_col}",
                title: "{title}",
                ylabel: "{ylabel}"
            }};
        </script>
        <script src="/static/chart.js"></script>
    </body>
    </html>
    """
    return html

def trends_component():
    results_dir = get_results_dir()
    
    if not os.path.exists(results_dir):
        me.text(f"Results directory not found at {results_dir}")
        return
        
    cache_file = os.path.join(results_dir, "trends_cache.csv")
    
    df = None
    
    # Try to load from cache
    if os.path.exists(cache_file):
        try:
            df = pd.read_csv(cache_file)
            logging.info("Loaded trends data from cache.")
        except Exception as e:
            logging.error(f"Error reading cache file: {e}")
            
    # Fallback to computing on the fly if cache is missing or failed
    if df is None:
        directories = [
            d
            for d in os.listdir(results_dir)
            if os.path.isdir(os.path.join(results_dir, d))
        ]
        
        data = []
        
        for d in directories:
            run_dir = os.path.join(results_dir, d)
            configs_file = os.path.join(run_dir, "configs.csv")
            summary_file = os.path.join(run_dir, "summary.csv")
            
            if os.path.exists(configs_file) and os.path.exists(summary_file):
                try:
                    configs_df = pd.read_csv(configs_file)
                    
                    requester_row = configs_df[configs_df['config'].str.contains('guitar_requester', na=False)]
                    product_row = configs_df[configs_df['config'].isin(['experiment_config.product_name', 'experiment_config.poduct_name'])]
                    generator_row = configs_df[configs_df['config'] == 'model_config.generator']
                    
                    requester = requester_row['value'].values[0] if not requester_row.empty else "unknown"
                    product = product_row['value'].values[0] if not product_row.empty else "unknown"
                    dataset_path = configs_df[configs_df['config'] == 'experiment_config.dataset_config']['value'].values[0] if 'experiment_config.dataset_config' in configs_df['config'].values else "unknown"
                    dataset = os.path.basename(dataset_path) if dataset_path != "unknown" else "unknown"
                    generator = generator_row['value'].values[0] if not generator_row.empty else "unknown"
                    
                    summary_df = pd.read_csv(summary_file)
                    
                    latency_row = summary_df[summary_df['metric_name'] == 'end_to_end_latency']
                    token_row = summary_df[summary_df['metric_name'] == 'token_consumption']
                    trajectory_row = summary_df[summary_df['metric_name'] == 'trajectory_matcher']
                    
                    latency = float(latency_row['metric_score'].values[0]) if not latency_row.empty else 0.0
                    tokens = float(token_row['metric_score'].values[0]) if not token_row.empty else 0.0
                    trajectory = float(trajectory_row['metric_score'].values[0]) if not trajectory_row.empty else 0.0
                    
                    run_time = summary_df['run_time'].values[0] if not summary_df.empty else "unknown"
                    if run_time != "unknown":
                        try:
                            run_time = pd.to_datetime(run_time).strftime('%Y-%m-%d')
                        except Exception as e:
                            logging.warning(f"Failed to parse run_time '{run_time}': {e}")
                    
                    data.append({
                        'run_time': run_time,
                        'requester': requester,
                        'product': product,
                        'dataset': dataset,
                        'model_config.generator': generator,
                        'latency': latency,
                        'tokens': tokens,
                        'trajectory': trajectory,
                        'job_id': d,
                        'ai_score': 0.0
                    })
                except Exception as e:
                    logging.error(f"Error reading data from {d}: {e}")
                    
        if not data:
            me.text("No data found in any run directory.")
            return
            
        df = pd.DataFrame(data)
        
    # Filter by requester
    df = df[df['requester'] == 'cloud-db-nl2sql-testing-jobs']
    
    # Create product_dataset column for combined line
    df['product_dataset'] = df['product'] + " (" + df['dataset'] + ")"
    
    # Filter by product (remove unknown or empty)
    df = df[df['product'].notna() & (df['product'] != 'unknown') & (df['product'].str.strip() != '')]
    
    state = me.state(State)
    
    # Apply agent tab filter
    if state.trends_agent_tab == "Gemini":
        df = df[df['model_config.generator'].str.contains('gemini', case=False) | (df['model_config.generator'] == 'unknown') | (df['model_config.generator'] == 'N/A') | df['product'].isin(['spanner', 'bigtable', 'alloydb', 'memorystore', 'dms', 'datastream'])]
    elif state.trends_agent_tab == "Claude":
        df = df[df['model_config.generator'].str.contains('claude', case=False) | ((df['model_config.generator'] == 'unknown') & df['product'].str.contains('claude', case=False))]
    elif state.trends_agent_tab == "Codex":
        df = df[df['model_config.generator'].str.contains('codex', case=False)]
    elif state.trends_agent_tab == "Antigravity":
        df = df[df['model_config.generator'].str.contains('agy', case=False)]

    # Extract unique products for dropdown
    all_products = sorted(df['product'].unique().tolist())
    logging.info(f"All products found in trends: {all_products}")
    
    # Apply filter if selected
    if state.trends_product_filter:
        df = df[df['product'] == state.trends_product_filter]
    
    if df.empty:
        with me.box(
            style=me.Style(
                padding=me.Padding.all("24px"),
                background="#f8fafc",
                border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0")),
                border_radius="8px",
                text_align="center",
                color="#64748b",
                width="100%",
            )
        ):
            me.text("No data found for the selected filters.", style=me.Style(font_size="16px", font_weight="500", color="#475569"))
            me.text("Try adjusting your agent or product selection.", style=me.Style(font_size="14px", color="#64748b", margin=me.Margin(top="8px")))
        return
        
    # Generate charts
    latency_chart = generate_d3_chart(df, 'run_time', 'latency', 'product_dataset', 'Latency Trend', 'Latency (ms)')
    token_chart = generate_d3_chart(df, 'run_time', 'tokens', 'product_dataset', 'Token Consumption Trend', 'Tokens')
    trajectory_chart = generate_d3_chart(df, 'run_time', 'trajectory', 'product_dataset', 'Trajectory Score Trend', 'Score (%)')
    ai_score_chart = generate_d3_chart(df, 'run_time', 'ai_score', 'product_dataset', 'AI Score Trend', 'Score')

    
    # Render charts
    with me.box(style=me.Style(display="flex", flex_direction="column", gap="24px", padding=me.Padding.all("24px"), width="100%")):
        me.text("Trends for cloud-db-nl2sql-testing-jobs", style=me.Style(font_size="20px", font_weight="700"))
        
        # Add agent tabs for Charts view
        def on_trends_agent_tab_change(e):
            st = me.state(State)
            st.trends_agent_tab = e.value
            
        me.button_toggle(
            value=state.trends_agent_tab,
            buttons=[
                me.ButtonToggleButton(label="Gemini", value="Gemini"),
                me.ButtonToggleButton(label="Claude", value="Claude"),
                me.ButtonToggleButton(label="Codex", value="Codex"),
                me.ButtonToggleButton(label="Antigravity", value="Antigravity"),
            ],
            on_change=on_trends_agent_tab_change,
        )
        me.box(style=me.Style(height="16px"))
        
        # Render custom dropdown
        def toggle_trends_product_dropdown(e: me.ClickEvent):
            st = me.state(State)
            if st.open_dropdown == "trends_product":
                st.open_dropdown = ""
            else:
                st.open_dropdown = "trends_product"
                
        def make_product_handler(val):
            def handler(e: me.ClickEvent):
                st = me.state(State)
                logging.info(f"Product handler triggered for: {val}")
                st.trends_product_filter = val
                st.open_dropdown = ""
            
            safe_val = str(val).replace(" ", "_").replace(".", "_").replace("-", "_")
            handler_name = f"click_trends_prod_{safe_val}"
            handler.__name__ = handler_name
            globals()[handler_name] = handler
            return handler
            
        with me.box(style=me.Style(position="relative", width="300px")):
            with me.box(
                style=me.Style(
                    padding=me.Padding.all("12px"),
                    background="#f8fafc",
                    border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0")),
                    border_radius="6px",
                    cursor="pointer",
                    display="flex",
                    justify_content="space-between",
                    align_items="center",
                ),
                on_click=toggle_trends_product_dropdown,
            ):
                me.text(state.trends_product_filter if state.trends_product_filter else "All Products", style=me.Style(font_weight="500"))
                me.text("▼", style=me.Style(font_size="10px", color="#64748b"))
                
            if state.open_dropdown == "trends_product":
                with me.box(
                    style=me.Style(
                        position="absolute",
                        top="100%",
                        left="0",
                        z_index=10,
                        background="#ffffff",
                        border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0")),
                        border_radius="4px",
                        width="100%",
                        max_height="200px",
                        overflow_y="auto",
                    )
                ):
                    # All option
                    with me.box(
                        style=me.Style(padding=me.Padding.all("8px"), cursor="pointer"),
                        on_click=make_product_handler(""),
                    ):
                        me.text("All Products", style=me.Style(color="#1f2937"))
                        
                    # Product options
                    for p in all_products:
                        with me.box(
                            style=me.Style(padding=me.Padding.all("8px"), cursor="pointer"),
                            on_click=make_product_handler(p),
                        ):
                            me.text(p, style=me.Style(color="#1f2937"))
        
        with me.box(style=me.Style(display="flex", flex_direction="column", gap="16px", width="100%")):
            me.text("AI Score", style=me.Style(font_size="16px", font_weight="600"))
            me.html(ai_score_chart, mode="sandboxed", style=me.Style(width="100%", height="550px"))
            
            me.text("Latency", style=me.Style(font_size="16px", font_weight="600"))
            me.html(latency_chart, mode="sandboxed", style=me.Style(width="100%", height="550px"))
            
            me.text("Token Consumption", style=me.Style(font_size="16px", font_weight="600"))
            me.html(token_chart, mode="sandboxed", style=me.Style(width="100%", height="550px"))
            
            me.text("Trajectory Score", style=me.Style(font_size="16px", font_weight="600"))
            me.html(trajectory_chart, mode="sandboxed", style=me.Style(width="100%", height="550px"))
