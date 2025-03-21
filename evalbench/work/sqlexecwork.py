"""Work is the base class for all work items."""

from typing import Any
from databases import DB
from work import Work
from util.sanitizer import sanitize_sql
from queue import Queue


class SQLExecWork(Work):
    """SQLExecWork Generates SQL from the generator."""

    def __init__(
        self,
        db: DB,
        experiment_config: dict,
        eval_result: dict,
        db_queue: Queue,
    ):
        self.db = db
        self.experiment_config = experiment_config
        self.eval_result = eval_result
        self.db_queue = db_queue

    def run(self, work_config: Any = None) -> dict:
        """Runs the work item.

        Args:
          work_config:

        Returns:

        """
        generated_result = None
        generated_eval_result = None
        generated_error = None
        golden_result = None
        golden_eval_result = None
        golden_error = None

        if (
            self.eval_result["sql_generator_error"] is None
            and self.eval_result["generated_sql"]
        ):
            query_type = self.eval_result["query_type"]
            eval_query = self._get_eval_query()
            sanitized_generated_sql = self._sanitize_sql()
            golden_sql = self._get_golden_sql()

            if sanitized_generated_sql:
                generated_result, generated_eval_result, generated_error = (
                    self._evaluate_execution_results(
                        sanitized_generated_sql, eval_query, query_type, is_golden=False
                    )
                )
            golden_result, golden_eval_result, golden_error = (
                self._evaluate_execution_results(
                    golden_sql, eval_query, query_type, is_golden=True
                )
            )

        self.eval_result["generated_result"] = generated_result
        self.eval_result["eval_results"] = generated_eval_result
        self.eval_result["generated_error"] = generated_error
        self.eval_result["golden_result"] = golden_result
        self.eval_result["golden_eval_results"] = golden_eval_result
        self.eval_result["golden_error"] = golden_error

        self.db_queue.put(self.db)
        return self.eval_result

    def _evaluate_execution_results(
        self, query, eval_query, query_type, is_golden=False
    ):
        result = None
        eval_result = None
        error = None
        if query_type == "dql":
            result, _, error = self.db.execute(query, use_cache=True, rollback=True)
        elif query_type == "dml":
            # self.db.execute(self.eval_result["setup_sql"])
            result, eval_result, error = self.db.execute(
                query, eval_query, use_cache=False, rollback=True
            )
            # self.db.execute(self.eval_result["cleanup_sql"])
        elif query_type == "ddl":
            # self.db.execute(self.eval_result["setup_sql"])
            try:
                self.db.resetup_database(force=True)
            except Exception as setup_error:
                return (
                    None,
                    None,
                    f"Was not able to run DDL due to setup_error {setup_error}",
                )
            result, _, error = self.db.execute(query, use_cache=False)
            eval_result = self.db.get_metadata()
            # self.db.execute(self.eval_result["cleanup_sql"])
        return result, eval_result, error

    def _sanitize_sql(self):
        if self.experiment_config["prompt_generator"] == "NOOPGenerator":
            self.eval_result["sanitized_sql"] = self.eval_result["generated_sql"]
        else:
            self.eval_result["sanitized_sql"] = sanitize_sql(
                self.eval_result["generated_sql"]
            )
        return self.eval_result["sanitized_sql"]

    def _get_golden_sql(self):
        golden_sql = ""
        if isinstance(self.eval_result["golden_sql"], str):
            golden_sql = self.eval_result["golden_sql"]
        elif (
            isinstance(self.eval_result["golden_sql"], list)
            and len(self.eval_result["golden_sql"]) > 0
        ):
            golden_sql = self.eval_result["golden_sql"][0]
        return golden_sql

    def _get_eval_query(self):
        if self.eval_result["eval_query"] and len(self.eval_result["eval_query"]) > 0:
            return self.eval_result["eval_query"][0]
        else:
            return None
