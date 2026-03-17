import logging

import time
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


def _process_futures_with_timeout(
        futures_to_process,
        future_to_eval_map,
        timeout=600):
    """Yields (future, eval_output, timed_out) ensuring we never hang forever on deadlocked tasks."""
    uncompleted = set(futures_to_process)
    # The timeout resets whenever AT LEAST ONE future completes.
    # This prevents the whole stage from failing if it just has a lot of tasks.
    last_completion_time = time.time()

    while uncompleted:
        elapsed_since_last = time.time() - last_completion_time
        if elapsed_since_last > timeout:

            logging.error(
                f"Abandoning {
                    len(uncompleted)} hung futures after {timeout}s timeout.")
            for f in list(uncompleted):
                uncompleted.remove(f)
                yield f, future_to_eval_map[f], True
            break

        done, not_done = concurrent.futures.wait(
            uncompleted,
            timeout=10,
            return_when=concurrent.futures.FIRST_COMPLETED
        )

        if done:
            last_completion_time = time.time()

        for f in done:
            uncompleted.remove(f)
            yield f, future_to_eval_map[f], False


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
        self.task_timeout_seconds = runner_config.get(
            "task_timeout_seconds", 600)

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

        prompt_future_to_eval = {}
        for eval_input in dataset:
            eval_output = EvalOutput(eval_input)
            eval_output["job_id"] = job_id
            eval_output["run_time"] = run_time
            work = promptgenwork.SQLPromptGenWork(
                prompt_generator, eval_output)
            self.promptrunner.execute_work(work)
            prompt_future_to_eval[self.promptrunner.futures[-1]] = eval_output

        gen_future_to_eval = {}
        for future, eval_output, timed_out in _process_futures_with_timeout(
                self.promptrunner.futures, prompt_future_to_eval, timeout=self.task_timeout_seconds):
            if timed_out:
                eval_output["prompt_generator_error"] = "TimeoutError: Task hung for too long."
            else:
                try:
                    future.result()
                except Exception as e:

                    logging.error(f"Promptgen future error: {e}")
                    eval_output["prompt_generator_error"] = str(e)

            record_successful_prompt_gen(progress_reporting)
            work = sqlgenwork.SQLGenWork(model_generator, eval_output)
            self.genrunner.execute_work(work)
            gen_future_to_eval[self.genrunner.futures[-1]] = eval_output

        exec_future_to_eval = {}
        score_future_to_eval = {}
        for future, eval_output, timed_out in _process_futures_with_timeout(
                self.genrunner.futures, gen_future_to_eval, timeout=self.task_timeout_seconds):
            if timed_out:
                eval_output["sql_generator_error"] = "TimeoutError: Task hung for too long."
            else:
                try:
                    future.result()
                except Exception as e:

                    logging.error(f"SQLgen future error: {e}")
                    eval_output["sql_generator_error"] = str(e)

            record_successful_sql_gen(progress_reporting)

            try:
                db_conn = db_queue.get(timeout=60)
                work = sqlexecwork.SQLExecWork(
                    db_conn, self.config, eval_output, db_queue
                )
                self.sqlrunner.execute_work(work)
                exec_future_to_eval[self.sqlrunner.futures[-1]] = eval_output
            except Exception as e:

                logging.error(
                    f"Failed to acquire DB connection from queue: {e}")
                eval_output["generated_error"] = f"Failed to acquire DB connection: {e}"
                record_successful_sql_exec(progress_reporting)
                work = scorework.ScorerWork(
                    self.config, eval_output, scoring_results, global_models
                )
                self.scoringrunner.execute_work(work)
                score_future_to_eval[self.scoringrunner.futures[-1]
                                     ] = eval_output

        for future, eval_output, timed_out in _process_futures_with_timeout(
                self.sqlrunner.futures, exec_future_to_eval, timeout=self.task_timeout_seconds):
            if timed_out:
                eval_output["generated_error"] = "TimeoutError: Task hung for too long."
            else:
                try:
                    future.result()
                except Exception as e:

                    logging.error(f"SQLExec future error: {e}")
                    eval_output["generated_error"] = str(e)

            record_successful_sql_exec(progress_reporting)
            work = scorework.ScorerWork(
                self.config, eval_output, scoring_results, global_models
            )
            self.scoringrunner.execute_work(work)
            score_future_to_eval[self.scoringrunner.futures[-1]] = eval_output

        for future, eval_output, timed_out in _process_futures_with_timeout(
                self.scoringrunner.futures, score_future_to_eval, timeout=self.task_timeout_seconds):
            if timed_out:
                eval_output["scoring_error"] = "TimeoutError: Task hung for too long."
            else:
                try:
                    future.result()
                except Exception as e:

                    logging.error(f"Scoring future error: {e}")
                    eval_output["scoring_error"] = str(e)

            record_successful_scoring(progress_reporting)
            try:
                truncateExecutionOutputs(
                    eval_output,
                    self.config,
                )
            except Exception as e:

                logging.error(f"Truncation error: {e}")
            eval_outputs.append(eval_output)

        if db_queue:
            while not db_queue.empty():
                db = db_queue.get()
                db.close_connections()

        return eval_outputs, scoring_results
