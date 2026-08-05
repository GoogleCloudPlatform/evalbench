import os
import logging
import json
import argparse
import pandas as pd

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# A pass used to submit every unprocessed run at once, hold all of it in memory,
# and write only after the last one finished. In production that pass never
# finished: it was SIGKILLed roughly every three minutes having got through ~183
# of 8166 directories, and because nothing had been written it restarted from the
# same 8166 every time. The cache stood still for six weeks.
#
# Batching bounds how much is in flight and checkpoints as it goes, so a kill
# costs one batch instead of the whole pass. The batch size is deliberately well
# under the ~183 directories a cycle managed before dying, so several checkpoints
# land inside a cycle even if the kills continue.
BATCH_SIZE = int(os.environ.get("PRECOMPUTE_BATCH_SIZE", 50))
MAX_WORKERS = int(os.environ.get("PRECOMPUTE_WORKERS", 16))


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
                    # Only the two columns needed. These files carry per-row
                    # comparison_logs blobs orders of magnitude larger than the
                    # scores, and every worker thread reading one in full is the
                    # largest allocation a batch makes.
                    columns = pd.read_csv(file_to_read, nrows=0).columns
                    if 'comparator' in columns and 'score' in columns:
                        df = pd.read_csv(file_to_read, usecols=['comparator', 'score'])
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


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    try:
        with open(path, "r") as f:
            return json.load(f)
    except Exception as e:
        logging.error(f"Error reading {path}: {e}")
        return default


def _write_json(path, data):
    with open(path, "w") as f:
        json.dump(data, f, indent=2)


def _cached_job_ids(cache_file):
    """Job ids already in the trends cache, read without the rest of the file.

    Only needed to keep a re-processed run from being appended twice; loading
    every column to find out would mean reading the whole cache back on startup.
    """
    if not os.path.exists(cache_file):
        return set()
    try:
        return set(pd.read_csv(cache_file, usecols=['job_id'])['job_id'].dropna())
    except Exception as e:
        logging.error(f"Error reading job ids from trends cache: {e}")
        return set()


def _append_rows(cache_file, rows):
    """Append a batch to the trends cache without reading the existing rows back."""
    new_df = pd.DataFrame(rows)
    # A zero-byte cache is the leftover of a write that was killed part way; it
    # has no header to append under, so start it over.
    if not os.path.exists(cache_file) or os.path.getsize(cache_file) == 0:
        new_df.to_csv(cache_file, index=False)
        return

    header = list(pd.read_csv(cache_file, nrows=0).columns)
    if set(header) != set(new_df.columns):
        # process_directory gained or lost a field since the cache was written.
        # One consistent header matters more than the memory this costs, and a
        # rewrite only happens on the first batch after such a change.
        logging.warning(
            "Trends cache columns changed; rewriting %s to match", cache_file
        )
        combined = pd.concat([pd.read_csv(cache_file), new_df], ignore_index=True)
        combined = combined.drop_duplicates(subset=['job_id'], keep='last')
        combined.to_csv(cache_file, index=False)
        return

    new_df[header].to_csv(cache_file, mode="a", header=False, index=False)


def precompute():
    results_dir = get_results_dir()
    logging.info(f"Reading results from {results_dir}")

    if not os.path.exists(results_dir):
        logging.warning(f"Results directory not found at {results_dir}")
        return

    processed_dirs_file = os.path.join(results_dir, "processed_dirs.json")
    cache_file = os.path.join(results_dir, "trends_cache.csv")
    filters_file = os.path.join(results_dir, "filters_cache.json")

    processed_dirs = set(_read_json(processed_dirs_file, []))
    logging.info(f"Loaded {len(processed_dirs)} processed directories from state.")

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

    # Carried forward from the last pass rather than recomputed off the trends
    # cache, which would mean loading every row of it.
    filters = _read_json(filters_file, {})
    products = set(filters.get("products", []))
    requesters = set(filters.get("requesters", []))
    datasets = set(filters.get("datasets", []))

    cached_job_ids = _cached_job_ids(cache_file)

    from concurrent.futures import ThreadPoolExecutor

    logging.info(
        f"Processing {total_new} new directories in batches of {BATCH_SIZE} "
        f"with {MAX_WORKERS} threads..."
    )

    for start in range(0, total_new, BATCH_SIZE):
        batch = new_directories[start:start + BATCH_SIZE]
        rows = []
        batch_processed = []

        with ThreadPoolExecutor(max_workers=MAX_WORKERS) as executor:
            futures = {
                executor.submit(process_directory, d, results_dir): d for d in batch
            }
            for future, directory in futures.items():
                try:
                    res = future.result()
                except Exception:
                    # One unreadable run must not cost the rest of the batch.
                    logging.exception(f"Error processing {directory}")
                    continue
                if not res:
                    # No summary.csv yet — likely still running, so leave it
                    # unprocessed and pick it up on a later pass.
                    continue

                batch_processed.append(res['job_id'])
                if res['job_id'] in cached_job_ids:
                    logging.info(f"{res['job_id']} already in trends cache; not re-appending")
                    continue

                rows.append(res)
                cached_job_ids.add(res['job_id'])
                if res['product'] != "unknown" and str(res['product']).strip() != "":
                    products.add(res['product'])
                if res['requester'] != "unknown" and str(res['requester']).strip() != "":
                    requesters.add(res['requester'])
                if res['dataset'] != "unknown":
                    datasets.add(res['dataset'])

        if rows:
            _append_rows(cache_file, rows)

        # Checkpoint after every batch: whatever this pass has managed so far
        # survives a kill, and the next pass resumes instead of restarting.
        processed_dirs.update(batch_processed)
        _write_json(processed_dirs_file, sorted(processed_dirs))

        # Filter values repeat heavily across runs, so most batches leave this
        # unchanged and there is no reason to rewrite it.
        new_filters = {
            "products": sorted(products),
            "requesters": sorted(requesters),
            "eval_ids": sorted(all_directories),
            "datasets": sorted(datasets),
        }
        if new_filters != filters:
            _write_json(filters_file, new_filters)
            filters = new_filters

        logging.info(
            f"Batch {start // BATCH_SIZE + 1}/{-(-total_new // BATCH_SIZE)}: "
            f"appended {len(rows)} rows, {len(processed_dirs)} directories processed."
        )

    logging.info(f"Precomputed trends data saved to {cache_file}")
    logging.info(f"Precomputed filter values saved to {filters_file}")
    logging.info(f"Saved {len(processed_dirs)} processed directories to state.")


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
