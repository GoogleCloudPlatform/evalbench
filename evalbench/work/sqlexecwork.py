"""Work is the base class for all work items."""

from typing import Any
from databases import DB
from work import Work
from util.sanitizer import sanitize_sql
from queue import Queue
import logging
import sqlparse
import traceback


def validate_and_normalize_execution_result(
    result: Any, connector_name: str = ""
) -> list[dict[str, Any]]:
    """Enforces the EvalBench execution result contract at the boundary.

    The contract requires that query execution returns a list of dictionaries,
    where each dictionary represents a row mapping column name to column value:
    list[dict[str, Any]].

    - None or empty sequence is normalized to an empty list [].
    - Non-empty sequence must contain dict rows; otherwise TypeError is raised to fail fast.
    """
    if result is None:
        return []
    if not isinstance(result, (list, tuple)):
        raise TypeError(
            f"Connector '{connector_name}' execute() returned invalid result type "
            f"'{type(result).__name__}'. Expected list[dict[str, Any]]."
        )
    if not result:
        return []

    for i, row in enumerate(result):
        if not isinstance(row, dict):
            raise TypeError(
                f"Connector '{connector_name}' execute() returned row {i} of type "
                f"'{type(row).__name__}': {row!r}. "
                "Each row must be a dict mapping column name to value (dict[str, Any]). "
                "If using a database cursor that yields tuples, convert each row with dict(zip(column_names, row))."
            )
    return list(result)


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
        try:
            return self._run_inner(work_config)
        finally:
            self.db_queue.put(self.db)

    def _run_inner(self, work_config: Any = None) -> dict:
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

        query_type = self.eval_result["query_type"]
        eval_query = self._get_eval_query()
        preprocess_sql = self._get_preprocess_sql_query()
        golden_sql = self._get_golden_sql()

        if golden_sql:
            golden_result, golden_eval_result, golden_error = (
                self._evaluate_execution_results(
                    golden_sql,
                    preprocess_sql,
                    eval_query,
                    query_type,
                    is_golden=True,
                )
            )

        if (
            self.eval_result["sql_generator_error"] is None
            and self.eval_result.get("generated_sql")
        ):
            sanitized_generated_sql = self._sanitize_sql()
            if sanitized_generated_sql:
                generated_result, generated_eval_result, generated_error = (
                    self._evaluate_execution_results(
                        sanitized_generated_sql,
                        preprocess_sql,
                        eval_query,
                        query_type,
                        is_golden=False,
                    )
                )

        self.eval_result["generated_result"] = generated_result
        self.eval_result["eval_results"] = generated_eval_result
        self.eval_result["generated_error"] = generated_error
        self.eval_result["golden_result"] = golden_result
        self.eval_result["golden_eval_results"] = golden_eval_result
        self.eval_result["golden_error"] = golden_error

        return self.eval_result

    def _evaluate_execution_results(
        self, query, preprocess_sql, eval_query, query_type, is_golden=False
    ):
        # Ensure query is a scalar string, joining if presented as a list
        if isinstance(query, list):
            query = "\n".join(str(q) for q in query)

        result = None
        eval_result = None
        error = None
        connector_name = type(self.db).__name__
        if preprocess_sql and not is_golden:
            try:
                self.db.execute(preprocess_sql)
            except Exception as preprocess_error:
                traceback.print_exc()

        if not query or not query.strip():
            return None, None, "list index out of range (empty query)"

        try:
            if query_type == "dql":
                stmts = sqlparse.split(query)
                if not stmts:
                    return None, None, "list index out of range (empty query)"
                result, _, error = self.db.execute(
                    stmts[0], use_cache=True, rollback=True
                )
            elif query_type == "dml":
                self.db.execute(self.eval_result["setup_sql"])
                result, eval_result, error = self.db.execute(
                    query, eval_query, use_cache=False, rollback=True
                )
            elif query_type == "ddl":
                # self.db.resetup_database(force=True)
                setup_sql = self.eval_result.get("setup_sql")
                if isinstance(setup_sql, dict):
                    setup_sql = setup_sql.get(self.db.dialect)
                elif isinstance(setup_sql, list) and len(setup_sql) > 0:
                    setup_sql = setup_sql[0]
                if setup_sql:
                    self.db.execute(setup_sql)
                result, _, error = self.db.execute(query, use_cache=False)
                eval_result = self.db.get_metadata()

            if error is None:
                result = validate_and_normalize_execution_result(
                    result, connector_name
                )
        except Exception as e:
            error = str(e)
            result = None
        finally:
            if query_type in ("dml", "ddl"):
                cleanup_sql = self.eval_result.get("cleanup_sql")
                if isinstance(cleanup_sql, dict):
                    cleanup_sql = cleanup_sql.get(self.db.dialect)
                elif isinstance(cleanup_sql, list) and len(cleanup_sql) > 0:
                    cleanup_sql = cleanup_sql[0]
                if cleanup_sql:
                    try:
                        self.db.execute(cleanup_sql)
                    except Exception:
                        pass

        if is_golden and error:
            logging.warning(
                "Golden SQL exec failed (id=%s db=%s): %s\nSQL: %s",
                self.eval_result.get("id"),
                getattr(self.db, "database", None),
                error,
                query,
            )
        return result, eval_result, error

    def _sanitize_sql(self):
        if (
            self.experiment_config["prompt_generator"] == "NOOPGenerator"
            and self.experiment_config.get("dialect") != "googlesql"
        ):
            self.eval_result["sanitized_sql"] = self.eval_result[
                "generated_sql"
            ]
        else:
            self.eval_result["sanitized_sql"] = sanitize_sql(
                self.eval_result["generated_sql"],
                dialect=self.experiment_config.get("dialect"),
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
        if self.eval_result["eval_query"] and len(
                self.eval_result["eval_query"]) > 0:
            return self.eval_result["eval_query"][0]
        else:
            return None

    def _get_preprocess_sql_query(self):
        if "preprocess_sql" in self.eval_result:
            if len(self.eval_result["preprocess_sql"]) > 0:
                return "".join(self.eval_result["preprocess_sql"])
            else:
                return None
        else:
            return None
