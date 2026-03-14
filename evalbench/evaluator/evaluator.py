from typing import Any, List
import datetime
from util import truncateExecutionOutputs
from work import promptgenwork
from work import sqlgenwork
from work import sqlexecwork
from work import scorework
from mp import mprunner
import concurrent.futures
from dataset.evalinput import EvalInputRequest
from dataset.evaloutput import EvalOutput
from evaluator.progress_reporter import (
    record_successful_prompt_gen,
    record_successful_sql_gen,
    record_successful_sql_exec,
    record_successful_scoring,
)
from queue import Queue
from databases import DB


class Evaluator:
    def __init__(
        self,
        config,
    ):
        self.config = config
        runner_config = self.config.get("runners", {})
        self.promptgen_runners = runner_config.get("promptgen_runners", 10)
        self.sqlgen_runners = runner_config.get("sqlgen_runners", 10)
        self.sqlexec_runners = runner_config.get("sqlexec_runners", 10)
        self.scoring_runners = runner_config.get("scoring_runners", 10)

    def evaluate(
        self,
        dataset: List[EvalInputRequest],
        db_queue: Queue[DB],
        prompt_generator,
        model_generator,
        job_id: str,
        run_time: datetime.datetime,
        progress_reporting,
        global_models,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []

        self.promptrunner = mprunner.MPRunner(self.promptgen_runners)
        self.genrunner = mprunner.MPRunner(self.sqlgen_runners)
        self.sqlrunner = mprunner.MPRunner(self.sqlexec_runners)
        self.scoringrunner = mprunner.MPRunner(self.scoring_runners)
        prompt_generator.setup()

        self.promptrunner.futures.clear()
        self.genrunner.futures.clear()
        self.sqlrunner.futures.clear()
        self.scoringrunner.futures.clear()

        for eval_input in dataset:
            eval_output = EvalOutput(eval_input)
            eval_output["job_id"] = job_id
            eval_output["run_time"] = run_time
            work = promptgenwork.SQLPromptGenWork(
                prompt_generator, eval_output)
            self.promptrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.promptrunner.futures):
            eval_output = future.result()
            record_successful_prompt_gen(progress_reporting)
            work = sqlgenwork.SQLGenWork(model_generator, eval_output)
            self.genrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.genrunner.futures):
            eval_output = future.result()
            record_successful_sql_gen(progress_reporting)
            work = sqlexecwork.SQLExecWork(
                db_queue.get(), self.config, eval_output, db_queue
            )
            self.sqlrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.sqlrunner.futures):
            eval_output = future.result()
            record_successful_sql_exec(progress_reporting)
            work = scorework.ScorerWork(
                self.config, eval_output, scoring_results, global_models
            )
            self.scoringrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.scoringrunner.futures):
            eval_output = future.result()
            record_successful_scoring(progress_reporting)
            truncateExecutionOutputs(
                eval_output,
                self.config,
            )
            eval_outputs.append(eval_output)

        if db_queue:
            while not db_queue.empty():
                db = db_queue.get()
                db.close_connections()

        return eval_outputs, scoring_results
