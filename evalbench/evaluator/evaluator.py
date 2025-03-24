from typing import Any, List
import uuid
import json
import datetime
import logging
import tempfile
from evaluator.db_manager import build_db_queue
from util import printProgressBar, truncateExecutionOutputs
from work import promptgenwork
from work import sqlgenwork
from work import sqlexecwork
from work import scorework
from mp import mprunner
import concurrent.futures
from dataset.evalinput import EvalInputRequest
from dataset.evaloutput import EvalOutput
from queue import Queue
from databases import DB
from dataset.dataset import breakdown_datasets_by_query_type


class Evaluator:
    def __init__(
        self,
        experiment_config,
        prompt_generator,
        model_generator,
        db_config,
        core_db,
        setup_config,
    ):
        self.eval_ids = None
        self.experiment_config = experiment_config
        self.prompt_generator = prompt_generator
        self.model_generator = model_generator
        self.db_config = db_config
        self.core_db = core_db
        self.setup_config = setup_config
        self.job_id = f"{uuid.uuid4()}"
        self.run_time = datetime.datetime.now()
        self.total_eval_outputs = []
        self.total_scoring_results = []

        runner_config = self.experiment_config.get("runners", {})
        self.promptgen_runners = runner_config.get("promptgen_runners", 10)
        self.sqlgen_runners = runner_config.get("sqlgen_runners", 10)
        self.sqlexec_runners = runner_config.get("sqlexec_runners", 10)
        self.scoring_runners = runner_config.get("scoring_runners", 10)

    def evaluate(self, dataset: List[EvalInputRequest]):
        """This wrapper breaks down evaluations by category of evaluations. (dql, dml, ddl).
        This allows the module to prepare the correct database connections as DDL queries
        require setting up and tearing down the databsae and DML queries require prevention
        of unintended consequences. Additionally, DQLs are run under a read-only user.
        """
        datasets_by_category, total_dataset_len = breakdown_datasets_by_query_type(
            dataset
        )
        for query_type in ["dql", "dml", "ddl"]:
            self._prepare_databases_and_run_evaluations(
                datasets_by_category, total_dataset_len, query_type
            )

    def _execute_evaluations(
        self,
        db_queue: Queue[DB],
        dataset: List[EvalInputRequest],
        dataset_len: int,
        job_id: str,
        run_time: datetime.datetime,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []

        self.promptrunner = mprunner.MPRunner(self.promptgen_runners)
        self.genrunner = mprunner.MPRunner(self.sqlgen_runners)
        self.sqlrunner = mprunner.MPRunner(self.sqlexec_runners)
        self.scoringrunner = mprunner.MPRunner(self.scoring_runners)
        self.prompt_generator.setup()

        prompt_i = 0
        gen_i = 0
        exec_i = 0
        score_i = 0

        self.promptrunner.futures.clear()
        self.genrunner.futures.clear()
        self.sqlrunner.futures.clear()
        self.scoringrunner.futures.clear()

        for eval_input in dataset:
            eval_output = EvalOutput(eval_input)
            eval_output["job_id"] = job_id
            eval_output["run_time"] = run_time
            work = promptgenwork.SQLPromptGenWork(self.prompt_generator, eval_output)
            self.promptrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.promptrunner.futures):
            eval_output = future.result()
            prompt_i = prompt_i + 1
            printProgressBar(
                prompt_i, dataset_len, prefix="Prompts:", suffix="Complete", length=50
            )
            work = sqlgenwork.SQLGenWork(self.model_generator, eval_output)
            self.genrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.genrunner.futures):
            eval_output = future.result()
            gen_i = gen_i + 1
            printProgressBar(
                gen_i, dataset_len, prefix="SQLGen:", suffix="Complete", length=50
            )
            work = sqlexecwork.SQLExecWork(
                db_queue.get(), self.experiment_config, eval_output, db_queue
            )
            self.sqlrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.sqlrunner.futures):
            eval_output = future.result()
            exec_i = exec_i + 1
            work = scorework.ScorerWork(
                self.experiment_config, eval_output, scoring_results
            )
            self.scoringrunner.execute_work(work)
            printProgressBar(
                exec_i, dataset_len, prefix="SQLExec:", suffix="Complete", length=50
            )

        for future in concurrent.futures.as_completed(self.scoringrunner.futures):
            eval_output = future.result()
            score_i = score_i + 1
            if "truncate_execution_outputs" in self.experiment_config:
                truncateExecutionOutputs(
                    eval_output,
                    self.experiment_config["truncate_execution_outputs"],
                )
            printProgressBar(
                score_i,
                dataset_len,
                prefix="Scoring:",
                suffix="Complete",
                length=50,
            )
            eval_outputs.append(eval_output)

        return eval_outputs, scoring_results

    def _prepare_databases_and_run_evaluations(
        self,
        datasets_by_category: dict[str, list[EvalInputRequest]],
        total_dataset_len,
        query_type: str,
    ):
        db_queue = None
        filtered_dataset = datasets_by_category[query_type]
        if len(filtered_dataset) == 0:
            return
        try:
            db_queue = build_db_queue(
                self.core_db,
                self.db_config,
                self.setup_config,
                query_type,
                min(self.sqlexec_runners, len(filtered_dataset)),
            )
            logging.info(f"Evaluating {len(filtered_dataset)} {query_type} queries.")
            eval_outputs, scoring_results = self._execute_evaluations(
                db_queue,
                filtered_dataset,
                total_dataset_len,
                self.job_id,
                self.run_time,
            )
            self.total_eval_outputs.extend(eval_outputs)
            self.total_scoring_results.extend(scoring_results)
        except Exception as e:
            logging.info(
                f"Skipping {len(filtered_dataset)} {query_type} queries as DB could "
                + f"not be setup properly due to {e}."
            )
            return
        finally:
            if db_queue:
                while not db_queue.empty():
                    db = db_queue.get()
                    db.close_connections()

    def process(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            json.dump(self.total_eval_outputs, f, sort_keys=True, indent=4, default=str)
            results_tf = f.name
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            json.dump(
                self.total_scoring_results, f, sort_keys=True, indent=4, default=str
            )
            scores_tf = f.name
        return (
            self.job_id,
            self.run_time,
            results_tf,
            scores_tf,
        )
