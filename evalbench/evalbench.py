"""EvalBench is a framework to measure the quality of a generative AI (GenAI) workflow."""

from collections.abc import Sequence
from absl import app
from absl import flags
from util.config import load_yaml_config, config_to_df
from repository import get_repository
from dataset.dataset import load_json, load_dataset_from_json
import generators.models as models
import generators.prompts as prompts
import evaluator.evaluator as evaluator
import reporting.report as report
import reporting.bqstore as bqstore
import reporting.analyzer as analyzer
import databases
import setup_teardown
import logging
import json

logging.getLogger().setLevel(logging.INFO)

_EXPERIMENT_CONFIG = flags.DEFINE_string(
    "experiment_config",
    "configs/experiment_config.yaml",
    "Path to the eval execution configuration file.",
)


# evalbench.py
def main(argv: Sequence[str]) -> None:
    logging.info("EvalBench v1.0.0")

    experiment_config = load_yaml_config(_EXPERIMENT_CONFIG.value)
    if experiment_config == "":
        return

    logging.info("Loaded %s", _EXPERIMENT_CONFIG.value)

    repo = get_repository(experiment_config)
    repo.clone()

    db_config_yaml = experiment_config["database_config"]
    model_config_yaml = experiment_config["model_config"]
    dataset_config_json = experiment_config["dataset_config"]

    # Load the dataset
    dataset, database = load_dataset_from_json(dataset_config_json, experiment_config)

    # Load the model config
    model_config = load_yaml_config(model_config_yaml)

    # Load the DB config DB name comes from the dataset
    db_config = load_yaml_config(db_config_yaml)
    db_config["database_name"] = database
    model_config["database_config"] = db_config

    if "setup_config" in experiment_config:
        setup_teardown.setupDatabase(db_config=db_config, experiment_config=experiment_config,
                                     database=database, create_user=True)
    db = databases.get_database(db_config)

    # Load the Query Generator
    model_generator = models.get_generator(model_config)

    # Load the Prompt Generator
    prompt_generator = prompts.get_generator(db, experiment_config)

    # Load the evaluator
    eval = evaluator.Evaluator(experiment_config, prompt_generator, model_generator, db)
    job_id, run_time = eval.evaluate(dataset)

    config_df = config_to_df(job_id, run_time, experiment_config, model_config, db_config)
    report.store(config_df, bqstore.STORETYPE.CONFIGS)

    results = load_json(f"/tmp/eval_output_{job_id}.json")
    results_df = report.quick_summary(results)
    report.store(results_df, bqstore.STORETYPE.EVALS)

    scores = load_json(f"/tmp/score_result_{job_id}.json")
    scores_df, summary_scores_df = analyzer.analyze_result(scores, experiment_config)
    summary_scores_df["job_id"] = job_id
    summary_scores_df["run_time"] = run_time

    report.store(scores_df, bqstore.STORETYPE.SCORES)
    report.store(summary_scores_df, bqstore.STORETYPE.SUMMARY)


if __name__ == "__main__":
    app.run(main)
