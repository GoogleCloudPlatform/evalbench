import collections
import concurrent.futures
import datetime
import logging
from queue import Queue
import time
from typing import Any, List
from databases import DB
from dataset.evalinput import EvalInputRequest
from dataset.evaloutput import EvalOutput
from evaluator.progress_reporter import (
    record_successful_prompt_gen,
    record_successful_scoring,
    record_successful_sql_exec,
    record_successful_sql_gen,
)
from mp import mprunner
from util import truncateExecutionOutputs
from work import multi_trial_scorework
from work import promptgenwork
from work import scorework
from work import sqlexecwork
from work import sqlgenwork


def _process_futures_with_timeout(
    futures_to_process, future_to_eval_map, timeout=600
):
    """Yields (future, eval_output, timed_out) ensuring we never hang forever on deadlocked tasks."""
    uncompleted = set(futures_to_process)
    # The timeout resets whenever AT LEAST ONE future completes.
    # This prevents the whole stage from failing if it just has a lot of tasks.
    last_completion_time = time.time()

    while uncompleted:
        elapsed_since_last = time.time() - last_completion_time
        if elapsed_since_last > timeout:

            logging.error(
                f"Abandoning {len(uncompleted)} hung futures after {timeout}s"
                " timeout."
            )
            for f in list(uncompleted):
                uncompleted.remove(f)
                yield f, future_to_eval_map[f], True
            break

        done, not_done = concurrent.futures.wait(
            uncompleted, timeout=10, return_when=concurrent.futures.FIRST_COMPLETED
        )

        if done:
            last_completion_time = time.time()

        for f in done:
            uncompleted.remove(f)
            yield f, future_to_eval_map[f], False


class Evaluator:
    """Orchestrates the evaluation pipeline."""

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
        self.num_trials = self.config.get("num_trials", 1)

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
        close_connections=True,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        multi_trial_scoring_results: List[Any] = []

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
        prompt_future_to_input = {}
        for eval_input in dataset:
            eval_output = EvalOutput(eval_input)
            eval_output["job_id"] = job_id
            eval_output["run_time"] = run_time
            work = promptgenwork.SQLPromptGenWork(
                prompt_generator, eval_output)
            self.promptrunner.execute_work(work)
            prompt_future_to_eval[self.promptrunner.futures[-1]] = eval_output
            prompt_future_to_input[self.promptrunner.futures[-1]] = eval_input

        gen_future_to_eval = {}
        for future, eval_output, timed_out in _process_futures_with_timeout(
            self.promptrunner.futures,
            prompt_future_to_eval,
            timeout=self.task_timeout_seconds,
        ):
            if timed_out:
                eval_output["prompt_generator_error"] = (
                    "TimeoutError: Task hung for too long."
                )
            else:
                try:
                    future.result()
                except Exception as e:

                    logging.error(f"Promptgen future error: {e}")
                    eval_output["prompt_generator_error"] = str(e)

            record_successful_prompt_gen(progress_reporting)

            eval_input = prompt_future_to_input[future]

            query_type = eval_output.get("query_type", "dql").lower()
            trials_to_run = self.num_trials if query_type == "dql" else 1
            for trial_idx in range(trials_to_run):
                trial_output = EvalOutput(eval_input)
                trial_output["job_id"] = job_id
                trial_output["run_time"] = run_time
                trial_output.update(eval_output)

                trial_output["prompt_id"] = eval_output["id"]
                trial_output["trial_index"] = trial_idx
                trial_output["id"] = f"{eval_output['id']}_trial_{trial_idx}"

                work = sqlgenwork.SQLGenWork(model_generator, trial_output)
                self.genrunner.execute_work(work)
                gen_future_to_eval[self.genrunner.futures[-1]] = trial_output

        exec_future_to_eval = {}
        score_future_to_eval = {}
        for future, eval_output, timed_out in _process_futures_with_timeout(
            self.genrunner.futures,
            gen_future_to_eval,
            timeout=self.task_timeout_seconds,
        ):
            if timed_out:
                eval_output["sql_generator_error"] = (
                    "TimeoutError: Task hung for too long."
                )
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
                    "Failed to acquire DB connection from queue for database"
                    f" '{eval_output.get('database')}': {e}"
                )
                eval_output["generated_error"] = f"Failed to acquire DB connection: {e}"
                record_successful_sql_exec(progress_reporting)
                work = scorework.ScorerWork(
                    self.config, eval_output, scoring_results, global_models
                )
                self.scoringrunner.execute_work(work)
                score_future_to_eval[self.scoringrunner.futures[-1]
                                     ] = eval_output

        for future, eval_output, timed_out in _process_futures_with_timeout(
            self.sqlrunner.futures,
            exec_future_to_eval,
            timeout=self.task_timeout_seconds,
        ):
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
            self.scoringrunner.futures,
            score_future_to_eval,
            timeout=self.task_timeout_seconds,
        ):
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

        if self.num_trials > 1:
            grouped_trials = collections.defaultdict(list)
            for eo in eval_outputs:
                prompt_id = eo.get("prompt_id")
                if prompt_id:
                    grouped_trials[prompt_id].append(eo)

            multi_trial_futures = {}
            for prompt_id, trials in grouped_trials.items():
                nl_prompt = trials[0].get("nl_prompt", "")
                work = multi_trial_scorework.MultiTrialScorerWork(
                    prompt_id,
                    nl_prompt,
                    trials,
                    self.config,
                    multi_trial_scoring_results,
                    global_models,
                    progress_reporting,
                )
                self.scoringrunner.execute_work(work)
                multi_trial_futures[self.scoringrunner.futures[-1]] = trials[0]

            for future, _, timed_out in _process_futures_with_timeout(
                list(multi_trial_futures.keys()),
                multi_trial_futures,
                timeout=self.task_timeout_seconds,
            ):
                if timed_out:
                    logging.error("Multi-trial scoring timed out.")
                else:
                    try:
                        future.result()
                    except Exception as e:
                        logging.error(f"Multi-trial scoring future error: {e}")

        if close_connections and db_queue:
            import queue

            while True:
                try:
                    db = db_queue.get(block=False)
                    db.close_connections()
                except queue.Empty:
                    break
                except Exception:
                    break

        return eval_outputs, scoring_results, multi_trial_scoring_results
