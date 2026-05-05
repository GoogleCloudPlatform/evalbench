import logging
import datetime
import json
import tempfile
import threading
import uuid

import databases
import generators.models as models
import generators.prompts as prompts
from dataset.evalinput import EvalInputRequest
from evaluator.db_manager import build_db_queue
from evaluator.evaluator import Evaluator
from evaluator.orchestrator import Orchestrator


class StreamingOrchestrator(Orchestrator):
    """An orchestrator that evaluates items one-by-one as they arrive,
    caching db connections and generators by (dialect, database) key."""

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
        self.total_multi_trial_scoring_results = []
        self.reporting_total_evals_done = 0
        self.report_progress = report_progress

        runner_config = self.config.get("runners", {})
        self.eval_runners = runner_config.get("eval_runners", 4)
        self.sqlexec_runners = runner_config.get("sqlexec_runners", 10)

        # Caches keyed by (dialect, database)
        self._core_db_cache = {}
        self._prompt_gen_cache = {}
        self._model_gen_cache = {}
        # Cache keyed by (dialect, database, query_type)
        self._db_queue_cache = {}

        self._global_models = {
            "registered_models": {}, "lock": threading.Lock()}
        self._cache_lock = threading.Lock()
        self._results_lock = threading.Lock()

    def evaluate_item(self, eval_input: EvalInputRequest):
        """Evaluate a single item immediately using cached resources."""
        for dialect in eval_input.dialects:
            db_configs = self.db_configs.get(dialect)
            if not db_configs:
                continue

            item = eval_input.copy_for_dialect(dialect)
            for db_config in db_configs:
                self._evaluate_single(item, db_config, dialect)

    def _get_or_create_resources(self, db_key, queue_key, dialect, database, query_type, db_config):
        """Thread-safe lazy initialization of cached resources."""
        with self._cache_lock:
            # Get or create core_db
            core_db = self._core_db_cache.get(db_key)
            if core_db is None:
                actual_db_name = self._resolve_db_name(dialect, database)
                core_db = databases.get_database(db_config, actual_db_name)
                self._core_db_cache[db_key] = core_db

            # Get or create prompt_generator
            if db_key not in self._prompt_gen_cache:
                self._prompt_gen_cache[db_key] = prompts.get_generator(
                    core_db, self.config
                )

            # Get or create model_generator
            if db_key not in self._model_gen_cache:
                self._model_gen_cache[db_key] = models.get_generator(
                    self._global_models, self.config["model_config"], core_db
                )

            # Get or create db_queue
            if queue_key not in self._db_queue_cache:
                actual_db_name = self._resolve_db_name(dialect, database)
                db_queue = build_db_queue(
                    core_db,
                    actual_db_name,
                    db_config,
                    self.setup_config,
                    query_type,
                    self.eval_runners,
                )
                self._db_queue_cache[queue_key] = db_queue

            return (
                self._db_queue_cache[queue_key],
                self._prompt_gen_cache[db_key],
                self._model_gen_cache[db_key],
            )

    def _evaluate_single(self, eval_input: EvalInputRequest, db_config, dialect):
        database = eval_input.database
        query_type = eval_input.query_type
        db_key = (dialect, database, id(db_config))
        queue_key = (dialect, database, query_type, id(db_config))

        try:
            db_queue, prompt_gen, model_gen = self._get_or_create_resources(
                db_key, queue_key, dialect, database, query_type, db_config
            )
        except Exception as e:
            logging.error(
                f"Could not initialize resources for {database} on {dialect}: {e}"
            )
            return

        evaluator = Evaluator(self.config)
        eval_outputs, scoring_results, multi_trial_scoring_results = evaluator.evaluate(
            [eval_input],
            db_queue,
            prompt_gen,
            model_gen,
            self.job_id,
            self.run_time,
            None,
            self._global_models,
            close_connections=False,
        )
        with self._results_lock:
            self.total_eval_outputs.extend(eval_outputs)
            self.total_scoring_results.extend(scoring_results)
            self.total_multi_trial_scoring_results.extend(
                multi_trial_scoring_results)

    def _resolve_db_name(self, dialect, database):
        db_name_overrides = self.config.get("db_name_overrides", {})
        db_name_mappings = self.config.get("db_name_mappings", {})
        if dialect in db_name_overrides and database in db_name_overrides[dialect]:
            return db_name_overrides[dialect][database]
        elif dialect in db_name_mappings:
            return db_name_mappings[dialect].format(db_id=database)
        return database

    def cleanup(self):
        """Clean up all cached connections."""
        for core_db in self._core_db_cache.values():
            try:
                core_db.clean_tmp_creations()
                core_db.close_connections()
            except Exception as e:
                logging.error(f"Error cleaning up db connection: {e}")

        for db_queue in self._db_queue_cache.values():
            try:
                while not db_queue.empty():
                    db = db_queue.get()
                    db.close_connections()
            except Exception as e:
                logging.error(f"Error cleaning up db queue: {e}")

        self._core_db_cache.clear()
        self._prompt_gen_cache.clear()
        self._model_gen_cache.clear()
        self._db_queue_cache.clear()

    def process(self):
        self.cleanup()
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".json"
        ) as f:
            json.dump(
                self.total_eval_outputs, f, sort_keys=True, indent=4, default=str
            )
            results_tf = f.name
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".json"
        ) as f:
            json.dump(
                self.total_scoring_results, f, sort_keys=True, indent=4, default=str
            )
            scores_tf = f.name
        with tempfile.NamedTemporaryFile(
            mode="w", delete=False, suffix=".json"
        ) as f:
            json.dump(
                self.total_multi_trial_scoring_results,
                f,
                sort_keys=True,
                indent=4,
                default=str,
            )
            multi_trial_scores_tf = f.name
        return (
            self.job_id,
            self.run_time,
            results_tf,
            scores_tf,
            multi_trial_scores_tf,
        )
