"""EvalBench is a framework to measure the quality of a generative AI (GenAI) workflow."""

from collections.abc import Sequence
from absl import app
from absl import flags
from reporting import get_reporters
from util.config import load_yaml_config, config_to_df
from dataset.dataset import load_json, load_dataset_from_json, flatten_dataset
from evaluator import get_orchestrator
import reporting.report as report
import reporting.analyzer as analyzer
import logging
from util.config import set_session_configs
from util.service import load_session_configs
import os
import sys
import yaml

try:
    import google.colab  # type: ignore

    _IN_COLAB = True
except ImportError:
    _IN_COLAB = False

logging.getLogger().setLevel(logging.INFO)

_EXPERIMENT_CONFIG = flags.DEFINE_string(
    "experiment_config",
    "configs/experiment_config.yaml",
    "Path to the eval execution configuration file.",
)


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
        if parsed_config == "":
            logging.error("No Eval Config Found.")
            return

        set_session_configs(session, parsed_config)
        # Load the configs
        config, db_configs, model_config, setup_config = load_session_configs(
            session)
        logging.info("Loaded Configurations in %s", experiment_config)

        # Load the dataset
        dataset = load_dataset_from_json(session["dataset_config"], config)
        # Load the evaluator
        evaluator = get_orchestrator(
            config, db_configs, setup_config, report_progress=True
        )

        # Run evaluations
        evaluator.evaluate(flatten_dataset(dataset))
        job_id, run_time, results_tf, scores_tf = evaluator.process()

        # Create Dataframes for reporting
        if results_tf is not None and scores_tf is not None:
            reporters = get_reporters(
                parsed_config.get("reporting"), job_id, run_time)
            config_df = config_to_df(
                job_id, run_time, config, model_config, db_configs)
            results = load_json(results_tf)
            results_df = report.get_dataframe(results)
            report.quick_summary(results_df)
            scores = load_json(scores_tf)
            scores_df, summary_scores_df = analyzer.analyze_result(
                scores, config)
            summary_scores_df["job_id"] = job_id
            summary_scores_df["run_time"] = run_time
        else:
            logging.warning(
                "There were no matching evals in this run. Returning empty set."
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
        return True
    except Exception as e:
        logging.exception(e)
        return False


def run_suite(suite_config_path: str) -> bool:
    with open(suite_config_path, 'r') as f:
        suite_conf = yaml.safe_load(f)

    runs = suite_conf.get("runs", [])
    if not runs:
        logging.error("No runs defined in suite config.")
        return False

    logging.info(
        f"Starting EvalBench Suite: {suite_conf.get('name', 'Unnamed Suite')}")
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
            f"\n{'=' * 50}\nExecuting Suite Run {i + 1}/{len(runs)}: {run_name}\nConfig: {config_path}\n{'=' * 50}")

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
        success = eval(experiment_config=_EXPERIMENT_CONFIG.value)

    exit_code = 0 if success else 1
    if _IN_COLAB:
        return sys.exit(exit_code)
    return os._exit(exit_code)


if __name__ == "__main__":
    app.run(main)
