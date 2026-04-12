import os
import logging
import mesop as me
import pandas as pd
from state import State

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
            body {{ 
                font-family: system-ui, -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Oxygen, Ubuntu, Cantarell, 'Open Sans', 'Helvetica Neue', sans-serif; 
                margin: 0;
                padding: 0;
            }}
            .axis-label {{ font-size: 12px; fill: #64748b; }}
            .line {{ fill: none; stroke-width: 3px; stroke-linecap: round; }}
            .area {{ opacity: 0.05; }}
            .dot {{ stroke: #fff; stroke-width: 2px; transition: r 0.2s, fill 0.2s; }}
            .dot:hover {{ r: 8px; cursor: pointer; }}
            .grid line {{ stroke: #e2e8f0; stroke-opacity: 0.7; shape-rendering: crispEdges; }}
            .grid path {{ stroke-width: 0; }}
            .tooltip {{
                position: absolute;
                text-align: left;
                padding: 12px;
                font-size: 14px;
                background: rgba(255, 255, 255, 0.95);
                border: 1px solid #e2e8f0;
                border-radius: 8px;
                pointer-events: none;
                opacity: 0;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
                backdrop-filter: blur(4px);
                transition: opacity 0.2s;
            }}
            .legend {{ font-size: 14px; fill: #334155; font-weight: 500; }}
            .chart-title {{ font-size: 18px; font-weight: 700; fill: #0f172a; }}
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
                    
                    requester = requester_row['value'].values[0] if not requester_row.empty else "unknown"
                    product = product_row['value'].values[0] if not product_row.empty else "unknown"
                    
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
                        'latency': latency,
                        'tokens': tokens,
                        'trajectory': trajectory,
                        'job_id': d
                    })
                except Exception as e:
                    logging.error(f"Error reading data from {d}: {e}")
                    
        if not data:
            me.text("No data found in any run directory.")
            return
            
        df = pd.DataFrame(data)
        
    # Filter by requester
    df = df[df['requester'] == 'cloud-db-nl2sql-testing-jobs']
    
    # Filter by product (remove unknown or empty)
    df = df[df['product'].notna() & (df['product'] != 'unknown') & (df['product'].str.strip() != '')]
    
    # Extract unique products for dropdown
    all_products = sorted(df['product'].unique().tolist())
    
    state = me.state(State)
    
    # Apply filter if selected
    if state.trends_product_filter:
        df = df[df['product'] == state.trends_product_filter]
    
    if df.empty:
        me.text("No data found for selected filters.")
        return
        
    # Generate charts
    latency_chart = generate_d3_chart(df, 'run_time', 'latency', 'product', 'Latency Trend', 'Latency (ms)')
    token_chart = generate_d3_chart(df, 'run_time', 'tokens', 'product', 'Token Consumption Trend', 'Tokens')
    trajectory_chart = generate_d3_chart(df, 'run_time', 'trajectory', 'product', 'Trajectory Score Trend', 'Score (%)')

    
    # Render charts
    with me.box(style=me.Style(display="flex", flex_direction="column", gap="24px", padding=me.Padding.all("24px"), width="100%")):
        me.text("Trends for cloud-db-nl2sql-testing-jobs", style=me.Style(font_size="20px", font_weight="700"))
        
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
                st.trends_product_filter = val
                st.open_dropdown = ""
            handler.__name__ = f"click_trends_product_{val}"
            return handler
            
        with me.box(style=me.Style(display="flex", align_items="center", gap="8px", margin=me.Margin(bottom="16px"))):
            me.text("Filter by Product:", style=me.Style(font_weight="600"))
            
            with me.box(style=me.Style(position="relative", width="200px")):
                # Trigger
                with me.box(
                    style=me.Style(
                        background="#ffffff",
                        border=me.Border.all(me.BorderSide(width="1px", color="#e2e8f0")),
                        border_radius="4px",
                        padding=me.Padding.all("8px"),
                        cursor="pointer",
                    ),
                    on_click=toggle_trends_product_dropdown,
                ):
                    me.text(
                        state.trends_product_filter if state.trends_product_filter else "All Products",
                        style=me.Style(color="#1f2937"),
                    )
                    
                # Popup
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
            me.text("Latency", style=me.Style(font_size="16px", font_weight="600"))
            me.html(latency_chart, mode="sandboxed", style=me.Style(width="100%", height="550px"))
            
            me.text("Token Consumption", style=me.Style(font_size="16px", font_weight="600"))
            me.html(token_chart, mode="sandboxed", style=me.Style(width="100%", height="550px"))
            
            me.text("Trajectory Score", style=me.Style(font_size="16px", font_weight="600"))
            me.html(trajectory_chart, mode="sandboxed", style=me.Style(width="100%", height="550px"))
