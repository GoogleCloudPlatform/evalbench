import pathlib

from absl import logging
from databases import get_database
from util.config import (
    config_to_df,
)
from dataset.dataset import load_json
from dataset import evalinput
import generators.models as models
import generators.prompts as prompts
import reporting.report as report
import reporting.bqstore as bqstore
import reporting.analyzer as analyzer
from evalproto import eval_response_pb2


def load_session_configs(session):
        return session["config"], session["db_config"], session["model_config"]

def create_eval_instances(config, db_config, model_config):
    core_db = get_database(db_config)
    model_generator = models.get_generator(core_db, model_config)
    prompt_generator = prompts.get_generator(core_db, config)
    return core_db, model_generator, prompt_generator

async def get_dataset_from_request(request_iterator):
    return [
        evalinput.EvalInputRequest.init_from_proto(request)
        async for request in request_iterator
    ]

def process_results(job_id, run_time, config, model_config, db_config):
    config_df = config_to_df(
        job_id,
        run_time,
        config,
        model_config,
        db_config,
    )
    report.store(config_df, bqstore.STORETYPE.CONFIGS)

    results = load_json(f"/tmp/eval_output_{job_id}.json")
    results_df = report.get_dataframe(results)
    if results_df.empty:
        logging.warning(
            "There were no matching evals in this run. Returning empty set."
        )
        return eval_response_pb2.EvalResponse(response=f"{job_id}")
    report.quick_summary(results_df)
    report.store(results_df, bqstore.STORETYPE.EVALS)

    scores = load_json(f"/tmp/score_result_{job_id}.json")
    scores_df, summary_scores_df = analyzer.analyze_result(scores, config)
    summary_scores_df["job_id"] = job_id
    summary_scores_df["run_time"] = run_time
    report.store(scores_df, bqstore.STORETYPE.SCORES)
    report.store(summary_scores_df, bqstore.STORETYPE.SUMMARY)

    # k8s emptyDir /tmp does not auto cleanup, so we explicitly delete
    pathlib.Path(f"/tmp/eval_output_{job_id}.json").unlink()
    pathlib.Path(f"/tmp/score_result_{job_id}.json").unlink()