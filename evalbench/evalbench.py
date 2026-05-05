"""EvalBench is a framework to measure the quality of a generative AI (GenAI) workflow."""

from collections.abc import Sequence
import logging
import multiprocessing
import os
import sys
from absl import app
from absl import flags
from dataset.dataset import flatten_dataset, load_dataset_from_json, load_json
from evaluator import get_orchestrator
from reporting import get_reporters
import reporting.analyzer as analyzer
import reporting.report as report
from util.config import config_to_df, load_yaml_config
from util.config import set_session_configs
from util.flags import EXPERIMENT_CONFIG
from util.scriptrunner import run_script
from util.service import load_session_configs
import yaml

try:
    import google.colab  # type: ignore

    _IN_COLAB = True
except ImportError:
    _IN_COLAB = False

logging.getLogger().setLevel(logging.INFO)


_SUITE_CONFIG = flags.DEFINE_string(
    "suite_config",
    None,
    "Path to a suite configuration file to run multiple experiments.",
)


def eval(experiment_config: str):
    try:
        logging.info("EvalBench v1.0.0")
        logging.getLogger("google_genai.models").setLevel(logging.WARNING)
        logging.getLogger("httpx").setLevel(logging.WARNING)
        os.environ["GRPC_VERBOSITY"] = "NONE"
        session: dict = {}

        parsed_config = load_yaml_config(experiment_config)

        # Helper logic to generate clean display path
        display_config = experiment_config
        g3_idx = display_config.find("google3/")
        if g3_idx != -1:
            display_config = display_config[g3_idx:]

        if parsed_config == "":
            logging.error(f"No Eval Config Found for '{display_config}'.")
            return

        set_session_configs(session, parsed_config)
        # Load the configs
        config, db_configs, model_config, setup_config = load_session_configs(
            session
        )
        logging.info("Loaded Configurations in %s", experiment_config)

        # Load the dataset
        dataset = load_dataset_from_json(session["dataset_config"], config)
        # Load the evaluator
        evaluator = get_orchestrator(
            config, db_configs, setup_config, report_progress=True
        )

        # Resolve session directory for local standalone logs
        reporting_config = config.get("reporting") or {}
        csv_config = reporting_config.get("csv") or {}
        base_output_dir = csv_config.get("output_directory", "results")
        session_dir = os.path.abspath(
            os.path.join(base_output_dir, evaluator.job_id)
        )

        set_up_script = config.get("set_up_script")
        if set_up_script:
            if os.path.exists(set_up_script):
                logging.info("Executing set_up_script '%s'", set_up_script)
                run_script(set_up_script, session_dir, "setup")
            else:
                logging.error(
                    "Cannot run set_up_script, file not found at '%s'", set_up_script
                )

        # Run evaluations
        evaluator.evaluate(flatten_dataset(dataset))
        job_id, run_time, results_tf, scores_tf, multi_trial_scores_tf = (
            evaluator.process()
        )

        # Create Dataframes for reporting
        if results_tf is not None and scores_tf is not None:
            reporters = get_reporters(
                parsed_config.get("reporting"), job_id, run_time
            )
            config_df = config_to_df(
                job_id, run_time, config, model_config, db_configs
            )
            results = load_json(results_tf)
            results_df = report.get_dataframe(results)
            report.quick_summary(results_df)
            scores = load_json(scores_tf)
            if multi_trial_scores_tf:
                multi_trial_scores = load_json(multi_trial_scores_tf)
                if multi_trial_scores:
                    scores.extend(multi_trial_scores)

            num_prompts = len(flatten_dataset(dataset))
            num_trials = config.get("num_trials", 1)
            scores_df, summary_scores_df = analyzer.analyze_result(
                scores, config, num_prompts=num_prompts, num_trials=num_trials
            )
            summary_scores_df["job_id"] = job_id
            summary_scores_df["run_time"] = run_time
        else:
            logging.warning(
                f"There were no matching evals in run for config '{display_config}'. Returning empty set."
            )
            reporters = []
            config_df = None
            results_df = None
            scores_df = None
            summary_scores_df = None

        # Store the reports in specified outputs
        for reporter in reporters:
            reporter.store(config_df, report.STORETYPE.CONFIGS)
            reporter.store(results_df, report.STORETYPE.EVALS)
            reporter.store(scores_df, report.STORETYPE.SCORES)
            reporter.store(summary_scores_df, report.STORETYPE.SUMMARY)
            reporter.print_dashboard_links()

        print(f"Finished Job ID {job_id}")

        tear_down_script = config.get("tear_down_script")
        if tear_down_script:
            if os.path.exists(tear_down_script):
                logging.info("Executing tear_down_script '%s'",
                             tear_down_script)
                run_script(tear_down_script, session_dir, "teardown")
            else:
                logging.error(
                    "Cannot run tear_down_script, file not found at '%s'",
                    tear_down_script,
                )

        return True
    except Exception as e:
        display_config = experiment_config
        g3_idx = display_config.find("google3/")
        if g3_idx != -1:
            display_config = display_config[g3_idx:]
        logging.exception(f"Evaluation failed for config '{display_config}': {e}")
        return False


def run_suite(suite_config_path: str) -> bool:
    with open(suite_config_path, "r") as f:
        suite_conf = yaml.safe_load(f)

    runs = suite_conf.get("runs", [])
    if not runs:
        logging.error("No runs defined in suite config.")
        return False

    logging.info(
        f"Starting EvalBench Suite: {suite_conf.get('name', 'Unnamed Suite')}"
    )
    logging.info(f"Total runs scheduled: {len(runs)}")

    results = []
    for i, run in enumerate(runs):
        run_name = run.get("name", f"Run {i + 1}")
        config_path = run.get("config_path")

        if not config_path:
            logging.error(
                f"Run '{run_name}' is missing 'config_path'. Skipping.")
            results.append((run_name, False))
            continue

        logging.info(
            f"\n{'=' * 50}\nExecuting Suite Run {i + 1}/{len(runs)}:"
            f" {run_name}\nConfig: {config_path}\n{'=' * 50}"
        )

        success = eval(config_path)
        results.append((run_name, success))

    logging.info(f"\n{'=' * 50}\nSuite Execution Summary:\n{'=' * 50}")
    all_passed = True
    for name, success in results:
        status = "SUCCESS" if success else "FAILED"
        logging.info(f"  - {name}: {status}")
        if not success:
            all_passed = False

    if not all_passed:
        logging.error("Some runs in the suite failed.")
    return all_passed


def main(argv: Sequence[str]):
    if _SUITE_CONFIG.value:
        success = run_suite(_SUITE_CONFIG.value)
    else:
        success = eval(experiment_config=EXPERIMENT_CONFIG.value)

    exit_code = 0 if success else 1
    if _IN_COLAB:
        return sys.exit(exit_code)
    return os._exit(exit_code)


if __name__ == "__main__":
    # Required for PyInstaller multiprocessing support
    multiprocessing.freeze_support()
    app.run(main)
