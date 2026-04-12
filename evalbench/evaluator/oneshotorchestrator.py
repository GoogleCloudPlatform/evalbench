import logging

import concurrent.futures
import datetime
import json

import tempfile
import threading
import uuid
from multiprocessing import Manager

import databases
import generators.models as models
import generators.prompts as prompts
from dataset.evalinput import EvalInputRequest, breakdown_datasets
from evaluator.db_manager import build_db_queue
from evaluator.evaluator import Evaluator
from evaluator.progress_reporter import (
    cleanup_progress_reporting,
    record_successful_setup,
    setup_progress_reporting,
    skip_database,
    skip_dialect,
)
from evaluator.orchestrator import Orchestrator


class OneShotOrchestrator(Orchestrator):
    def __init__(
        self,
        config,
        db_configs,
        setup_config,
        report_progress=False,
    ):
        self.config = config
        self.db_configs = db_configs
        self.setup_config = setup_config
        self.job_id = f"{uuid.uuid4()}"
        self.run_time = datetime.datetime.now()
        self.total_eval_outputs = []
        self.total_scoring_results = []
        self.reporting_total_evals_done = 0
        self.report_progress = report_progress

        runner_config = self.config.get("runners", {})
        self.eval_runners = runner_config.get("eval_runners", 4)
        self.sqlexec_runners = runner_config.get("sqlexec_runners", 10)

    def evaluate(self, dataset: list[EvalInputRequest]):
        """This wrapper breaks down evaluations by category of evaluations. (dql, dml, ddl).
        This allows the module to prepare the correct database connections as DDL queries
        require setting up and tearing down the databsae and DML queries require prevention
        of unintended consequences. Additionally, DQLs are run under a read-only user.
        """
        progress_reporting_thread = None
        progress_reporting_finished = None
        progress_reporting = None
        tmp_buffer = None
        colab_progress_report = None

        with Manager() as manager:
            sub_datasets, total_dataset_len, total_db_len = breakdown_datasets(
                dataset)
            try:
                if self.report_progress:
                    (
                        progress_reporting_thread,
                        progress_reporting,
                        progress_reporting_finished,
                        tmp_buffer,
                        colab_progress_report,
                    ) = setup_progress_reporting(
                        manager, total_dataset_len, total_db_len
                    )

                global_models = {"registered_models": {},
                                 "lock": threading.Lock()}

                with concurrent.futures.ThreadPoolExecutor(
                    max_workers=self.eval_runners
                ) as executor:
                    futures = []
                    for dialect in sub_datasets:
                        db_configs = self.db_configs.get(dialect)
                        if not db_configs:
                            logging.info(
                                f"Skipping queries for {dialect} as no applicable db_config" +
                                " was found.")
                            skip_dialect(
                                sub_datasets[dialect], progress_reporting)
                            continue
                        for db_config in db_configs:
                            for database in sub_datasets[dialect]:
                                future = executor.submit(
                                    self.evaluate_sub_dataset,
                                    sub_datasets,
                                    db_config,
                                    dialect,
                                    database,
                                    progress_reporting,
                                    global_models,
                                )
                                futures.append(future)
                    for future in concurrent.futures.as_completed(futures):
                        try:
                            # 24 hour timeout on the entire evaluator thread
                            # execution by default
                            timeout_seconds = self.config.get(
                                "orchestrator_timeout_seconds", 86400)
                            eval_outputs, scoring_results = future.result(
                                timeout=timeout_seconds)
                            self.total_eval_outputs.extend(eval_outputs)
                            self.total_scoring_results.extend(scoring_results)
                        except concurrent.futures.TimeoutError:

                            logging.error(
                                f"A runner thread timed out and failed to complete within {timeout_seconds} seconds.")
                        except Exception as e:

                            logging.error(f"A runner thread failed: {e}")

                if self.report_progress:
                    cleanup_progress_reporting(
                        progress_reporting, tmp_buffer, colab_progress_report
                    )
                    if progress_reporting_finished:
                        progress_reporting_finished.set()
                    if progress_reporting_thread:
                        progress_reporting_thread.join()
            except Exception as e:
                if progress_reporting:
                    cleanup_progress_reporting(
                        progress_reporting, tmp_buffer, colab_progress_report
                    )
                raise e

    def evaluate_sub_dataset(
        self,
        sub_datasets,
        db_config,
        dialect,
        database,
        progress_reporting,
        global_models,
    ):
        total_eval_outputs = []
        total_scoring_results = []

        # Map database name if config provides mappings
        actual_db_name = database
        db_name_mappings = self.config.get("db_name_mappings", {})
        db_name_overrides = self.config.get("db_name_overrides", {})

        if dialect in db_name_overrides and database in db_name_overrides[dialect]:
            actual_db_name = db_name_overrides[dialect][database]
        elif dialect in db_name_mappings:
            actual_db_name = db_name_mappings[dialect].format(db_id=database)

        try:
            # Setup the core connection just once (for all query types in
            # database)
            core_db = databases.get_database(db_config, actual_db_name)
        except Exception as e:
            skip_database(sub_datasets[dialect]
                          [database], progress_reporting, None)
            logging.error(
                f"Could not connect to database {actual_db_name} (from {database}) on {dialect}; due to {e}")
            return [], []

        prompt_generator = prompts.get_generator(core_db, self.config)
        model_generator = models.get_generator(
            global_models, self.config["model_config"], core_db
        )

        for query_type in ["dql", "dml", "ddl"]:
            if query_type not in sub_datasets[dialect][database]:
                continue
            sub_dataset = sub_datasets[dialect][database][query_type]
            sub_dataset_len = len(sub_dataset)
            db_queue = None
            try:
                db_queue = build_db_queue(
                    core_db,
                    actual_db_name,
                    db_config,
                    self.setup_config,
                    query_type,
                    min(self.sqlexec_runners, sub_dataset_len),
                )
                record_successful_setup(progress_reporting)
            except Exception as e:
                logging.info(
                    f"Skipping {query_type} queries as DB {database} "
                    + f"could not be setup properly in {dialect} due to {e}."
                )
                skip_database(
                    sub_datasets[dialect][database],
                    progress_reporting,
                    query_type)
                continue

            evaluator = Evaluator(self.config)
            try:
                eval_outputs, scoring_results = evaluator.evaluate(
                    sub_dataset,
                    db_queue,
                    prompt_generator,
                    model_generator,
                    self.job_id,
                    self.run_time,
                    progress_reporting,
                    global_models,
                )
                total_eval_outputs.extend(eval_outputs)
                total_scoring_results.extend(scoring_results)
            except Exception as e:
                logging.info(
                    f"Failed to evaluate {sub_dataset_len} {query_type} queries " +
                    f"on DB {database} on {dialect}. Due to {e}")

        # Cleanup all the tmp creations that were built from the core
        # connection
        if core_db:
            core_db.clean_tmp_creations()
            core_db.close_connections()

        return total_eval_outputs, total_scoring_results

    def process(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            json.dump(self.total_eval_outputs, f,
                      sort_keys=True, indent=4, default=str)
            results_tf = f.name
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            json.dump(
                self.total_scoring_results,
                f,
                sort_keys=True,
                indent=4,
                default=str)
            scores_tf = f.name
        return (
            self.job_id,
            self.run_time,
            results_tf,
            scores_tf,
        )
