"""Extract dataset quality grades from run artifacts into a single cache file.

The dataset quality scorer writes its report as JSON strings inside scores.csv,
which is far too expensive to parse across every run directory on each page
render. This walks new run directories once and keeps the table-level fields in
results/dataset_quality_cache.json; the detail view reads the one run it needs
straight from scores.csv.
"""

import argparse
import csv
import json
import logging
import os

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

CACHE_FILENAME = "dataset_quality_cache.json"
PROCESSED_DIRS_FILENAME = "dataset_quality_processed_dirs.json"

# Marks a run as dataset quality: these runs carry no experiment_config.product_name,
# so the scorer's own key is both the detector and the product label.
PRODUCT_CONFIG_KEY = "experiment_config.scorers.dataset_quality.product_name"

SUMMARY_COMPARATOR = "dataset_quality"

# Category JSON blobs embed per-CUJ evidence and prose, and scores.csv rows are
# correspondingly huge.
csv.field_size_limit(10**9)


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


def _read_configs(configs_file):
    """Return {config_key: value} plus the run_time carried on every row."""
    values = {}
    run_time = ""
    with open(configs_file, newline="") as f:
        for row in csv.DictReader(f):
            values[row.get("config", "")] = row.get("value", "")
            run_time = row.get("run_time", "") or run_time
    return values, run_time


def process_directory(job_id, results_dir):
    """Return the cache entry for a dataset quality run, or None for anything else."""
    run_dir = os.path.join(results_dir, job_id)
    configs_file = os.path.join(run_dir, "configs.csv")
    scores_file = os.path.join(run_dir, "scores.csv")
    if not (os.path.exists(configs_file) and os.path.exists(scores_file)):
        return None

    configs, run_time = _read_configs(configs_file)
    product_name = configs.get(PRODUCT_CONFIG_KEY)
    if not product_name:
        return None

    summary = {}
    category_scores = {}
    with open(scores_file, newline="") as f:
        for row in csv.DictReader(f):
            comparator = row.get("comparator", "")
            try:
                payload = json.loads(row.get("comparison_logs") or "{}")
            except json.JSONDecodeError:
                logging.warning(
                    "%s: unparseable comparison_logs for %s", job_id, comparator
                )
                continue
            if comparator == SUMMARY_COMPARATOR:
                summary = payload
            else:
                category_scores[comparator] = payload.get("score")

    dataset_config = configs.get("experiment_config.dataset_config", "")

    return {
        "job_id": job_id,
        "product_name": product_name,
        "run_time": run_time,
        "dataset": os.path.basename(dataset_config) if dataset_config else "",
        # A run that failed before grading records a null score, which must stay
        # distinct from a genuine F.
        "score": summary.get("dataset_quality_score"),
        "letter_grade": summary.get("letter_grade"),
        "total_cujs": summary.get("total_cujs"),
        "error": summary.get("error"),
        "category_scores": category_scores,
    }


def precompute():
    results_dir = get_results_dir()
    if not os.path.isdir(results_dir):
        logging.warning("Results directory not found: %s", results_dir)
        return

    cache_file = os.path.join(results_dir, CACHE_FILENAME)
    processed_dirs_file = os.path.join(results_dir, PROCESSED_DIRS_FILENAME)

    entries = {}
    if os.path.exists(cache_file):
        try:
            with open(cache_file) as f:
                entries = {e["job_id"]: e for e in json.load(f)}
        except Exception as e:
            logging.warning("Could not read %s, rebuilding: %s", cache_file, e)

    processed = set()
    if os.path.exists(processed_dirs_file):
        try:
            with open(processed_dirs_file) as f:
                processed = set(json.load(f))
        except Exception as e:
            logging.warning("Could not read %s, rebuilding: %s", processed_dirs_file, e)

    directories = [
        d for d in os.listdir(results_dir)
        if os.path.isdir(os.path.join(results_dir, d)) and d not in processed
    ]
    logging.info("Dataset quality: %d new directories to scan", len(directories))

    found = 0
    for job_id in directories:
        try:
            entry = process_directory(job_id, results_dir)
        except Exception:
            # A malformed run shouldn't be retried forever, but it also shouldn't
            # be marked processed until we know why it failed.
            logging.exception("Dataset quality: failed to process %s", job_id)
            continue
        processed.add(job_id)
        if entry:
            entries[job_id] = entry
            found += 1

    with open(cache_file, "w") as f:
        json.dump(sorted(entries.values(), key=lambda e: e["run_time"]), f, indent=2)
    with open(processed_dirs_file, "w") as f:
        json.dump(sorted(processed), f, indent=2)

    logging.info(
        "Dataset quality: %d new graded runs, %d total in %s",
        found, len(entries), cache_file,
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--clean", action="store_true", help="Delete cache files before processing"
    )
    args = parser.parse_args()

    if args.clean:
        results_dir = get_results_dir()
        for name in (CACHE_FILENAME, PROCESSED_DIRS_FILENAME):
            path = os.path.join(results_dir, name)
            if os.path.exists(path):
                os.remove(path)
                logging.info("Removed cache file: %s", path)

    precompute()
