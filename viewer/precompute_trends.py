import os
import logging
import json
import argparse
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

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


def process_directory(d, results_dir):
    run_dir = os.path.join(results_dir, d)
    configs_file = os.path.join(run_dir, "configs.csv")
    summary_file = os.path.join(run_dir, "summary.csv")

    logging.info(f"Checking files for {d}: configs={os.path.exists(configs_file)}, summary={os.path.exists(summary_file)}")
    if not (os.path.exists(configs_file) and os.path.exists(summary_file)):
        return None

    try:
        # Read configs
        configs_df = pd.read_csv(configs_file)

        # Extract requester, product, dataset and generator
        requester_row = configs_df[configs_df['config'].str.contains('guitar_requester', na=False)]
        product_row = configs_df[configs_df['config'].isin(['experiment_config.product_name', 'experiment_config.poduct_name'])]
        generator_row = configs_df[configs_df['config'] == 'model_config.generator']

        requester = requester_row['value'].values[0] if not requester_row.empty else "unknown"
        product = product_row['value'].values[0] if not product_row.empty else "unknown"
        dataset_path = configs_df[configs_df['config'] == 'experiment_config.dataset_config']['value'].values[0] if 'experiment_config.dataset_config' in configs_df['config'].values else "unknown"
        dataset = os.path.basename(dataset_path) if dataset_path != "unknown" else "unknown"
        generator = generator_row['value'].values[0] if not generator_row.empty else "unknown"

        # Read summary
        summary_df = pd.read_csv(summary_file)

        # Extract metrics
        latency_row = summary_df[summary_df['metric_name'] == 'end_to_end_latency']
        token_row = summary_df[summary_df['metric_name'] == 'token_consumption']
        trajectory_row = summary_df[summary_df['metric_name'] == 'trajectory_matcher']
        executable_row = summary_df[summary_df['metric_name'] == 'executable']
        turn_count_row = summary_df[summary_df['metric_name'] == 'turn_count']
        exact_match_row = summary_df[summary_df['metric_name'] == 'exact_match']
        llmrater_row = summary_df[summary_df['metric_name'] == 'llmrater']
        goal_completion_row = summary_df[summary_df['metric_name'] == 'goal_completion']

        latency = float(latency_row['metric_score'].values[0]) if not latency_row.empty else 0.0
        tokens = float(token_row['metric_score'].values[0]) if not token_row.empty else 0.0
        turn_count = float(turn_count_row['metric_score'].values[0]) if not turn_count_row.empty else 0.0

        def get_metric_pct(row):
            if not row.empty:
                correct = float(row['correct_results_count'].values[0])
                total = float(row['total_results_count'].values[0])
                return (correct / total) * 100 if total > 0 else 0.0
            return 0.0

        trajectory = get_metric_pct(trajectory_row)
        executable = get_metric_pct(executable_row)
        exact_match = get_metric_pct(exact_match_row)
        llmrater = get_metric_pct(llmrater_row)
        goal_completion = get_metric_pct(goal_completion_row)

        if goal_completion == 0.0 and goal_completion_row.empty:
            # Fallback to results.csv or scores.csv if goal_completion is missing from summary.csv
            results_file = os.path.join(results_dir, d, "results.csv")
            scores_file = os.path.join(results_dir, d, "scores.csv")

            file_to_read = None
            if os.path.exists(results_file):
                file_to_read = results_file
            elif os.path.exists(scores_file):
                file_to_read = scores_file

            if file_to_read:
                try:
                    df = pd.read_csv(file_to_read)
                    if 'comparator' in df.columns and 'score' in df.columns:
                        gc_scores = df[df['comparator'] == 'goal_completion']
                        if not gc_scores.empty:
                            correct = len(gc_scores[gc_scores['score'] == 100.0])
                            total = len(gc_scores)
                            goal_completion = (correct / total) * 100 if total > 0 else 0.0
                            logging.info(f"Computed goal_completion from {os.path.basename(file_to_read)} for {d}: {goal_completion}")
                except Exception as e:
                    logging.warning(f"Error reading {os.path.basename(file_to_read)} for {d}: {e}")

        run_time = summary_df['run_time'].values[0] if not summary_df.empty else "unknown"
        if run_time != "unknown":
            try:
                run_time = pd.to_datetime(run_time).strftime('%Y-%m-%d %H:%M:%S')
            except Exception as e:
                logging.warning(f"Failed to parse run_time '{run_time}': {e}")

        # Call AI Summarizer
        ai_summary = "N/A"
        ai_score = 0.0
        try:
            from summarizer import summarize_eval_scoring
            ai_summary = summarize_eval_scoring(run_dir)

            # Parse score from summary
            import re
            match = re.search(r"General Score.*?(\d+(\.\d+)?)", ai_summary, re.IGNORECASE)
            if match:
                ai_score = float(match.group(1))
        except Exception as e:
            logging.error(f"Error generating AI summary for {d}: {e}")

        logging.info(f"Successfully processed directory: {d}")
        return {
            'run_time': run_time,
            'requester': requester,
            'product': product,
            'dataset': dataset,
            'model_config.generator': generator,
            'latency': latency,
            'tokens': tokens,
            'trajectory': trajectory,
            'executable': executable,
            'turn_count': turn_count,
            'exact_match': exact_match,
            'llmrater': llmrater,
            'goal_completion': goal_completion,
            'job_id': d,
            'ai_score': ai_score,
            'ai_summary': ai_summary
        }
    except Exception as e:
        logging.exception(f"Error reading data from {d}")
        return None


def precompute():
    results_dir = get_results_dir()
    logging.info(f"Reading results from {results_dir}")
    
    if not os.path.exists(results_dir):
        logging.warning(f"Results directory not found at {results_dir}")
        return
        
    # Load processed directories
    processed_dirs_file = os.path.join(results_dir, "processed_dirs.json")
    processed_dirs = set()
    if os.path.exists(processed_dirs_file):
        try:
            with open(processed_dirs_file, "r") as f:
                processed_dirs = set(json.load(f))
            logging.info(f"Loaded {len(processed_dirs)} processed directories from state.")
        except Exception as e:
            logging.error(f"Error reading processed dirs file: {e}")

    # Load existing trends data
    cache_file = os.path.join(results_dir, "trends_cache.csv")
    existing_df = pd.DataFrame()
    if os.path.exists(cache_file):
        try:
            existing_df = pd.read_csv(cache_file)
            logging.info(f"Loaded {len(existing_df)} rows of existing trends data.")
        except Exception as e:
            logging.error(f"Error reading trends cache: {e}")

    all_directories = [
        d
        for d in os.listdir(results_dir)
        if os.path.isdir(os.path.join(results_dir, d))
    ]
    
    # Filter for new directories
    new_directories = [d for d in all_directories if d not in processed_dirs]
    
    total_new = len(new_directories)
    logging.info(f"Found {len(all_directories)} total directories. {total_new} are new.")
    
    if total_new == 0:
        logging.info("No new directories to process.")
        return

    data = []
    products = set()
    requesters = set()
    datasets = set()
    eval_ids = all_directories
    
    # If we have existing data, populate products and requesters from it
    if not existing_df.empty:
        if 'product' in existing_df.columns:
            products.update(existing_df['product'].dropna().unique())
        if 'requester' in existing_df.columns:
            requesters.update(existing_df['requester'].dropna().unique())

    successfully_processed = []
    
    from concurrent.futures import ThreadPoolExecutor
    
    logging.info(f"Processing {len(new_directories)} new directories with 50 threads...")
    with ThreadPoolExecutor(max_workers=50) as executor:
        futures = [executor.submit(process_directory, d, results_dir) for d in new_directories]
        
        for future in futures:
            res = future.result()
            if res:
                data.append(res)
                successfully_processed.append(res['job_id'])
                if res['product'] != "unknown" and str(res['product']).strip() != "":
                    products.add(res['product'])
                if res['requester'] != "unknown" and str(res['requester']).strip() != "":
                    requesters.add(res['requester'])
                if res['dataset'] != "unknown":
                    datasets.add(res['dataset'])
                
    if not data and existing_df.empty:
        logging.warning("No data found in any run directory and no existing cache.")
        return
        
    new_df = pd.DataFrame(data)
    
    # Combine with existing data
    if not existing_df.empty:
        df = pd.concat([existing_df, new_df], ignore_index=True)
    else:
        df = new_df
        
    # Drop duplicates just in case (based on job_id)
    df = df.drop_duplicates(subset=['job_id'], keep='last')
    
    # Save trends cache
    df.to_csv(cache_file, index=False)
    logging.info(f"Precomputed trends data saved to {cache_file}")
    
    # Save filters cache
    filters_file = os.path.join(results_dir, "filters_cache.json")
    filters_data = {
        "products": sorted(list(products)),
        "requesters": sorted(list(requesters)),
        "eval_ids": sorted(list(eval_ids)),
        "datasets": sorted(list(datasets))
    }
    with open(filters_file, "w") as f:
        json.dump(filters_data, f, indent=2)
    logging.info(f"Precomputed filter values saved to {filters_file}")
    
    # Save processed directories list
    new_processed_dirs = processed_dirs.union(set(successfully_processed))
    with open(processed_dirs_file, "w") as f:
        json.dump(list(new_processed_dirs), f, indent=2)
    logging.info(f"Saved {len(new_processed_dirs)} processed directories to state.")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--clean", action="store_true", help="Delete cache files before processing")
    args = parser.parse_args()
    
    if args.clean:
        results_dir = get_results_dir()
        cache_file = os.path.join(results_dir, "trends_cache.csv")
        filters_file = os.path.join(results_dir, "filters_cache.json")
        processed_dirs_file = os.path.join(results_dir, "processed_dirs.json")
        
        for f in [cache_file, filters_file, processed_dirs_file]:
            if os.path.exists(f):
                try:
                    os.remove(f)
                    logging.info(f"Removed cache file: {f}")
                except Exception as e:
                    logging.error(f"Error removing file {f}: {e}")
                
    precompute()
