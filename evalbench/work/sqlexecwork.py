"""Work is the base class for all work items."""

from typing import Any
from work import Work
import setup_teardown


class SQLExecWork(Work):
    """SQLExecWork Generates SQL from the generator."""

    def __init__(self, db: Any, experiment_config: dict, eval_result: dict, db_queue=None):
        self.db = db
        self.experiment_config = experiment_config
        self.eval_result = eval_result
        self.db_queue = db_queue

    def execute_sql_flow(self, query, rollback=False):
        if self.eval_result["query_type"] == "ddl":
            setup_teardown.setupDatabase(db_config=self.db.db_config, experiment_config=self.experiment_config,
                                         no_data=True, database=self.eval_result["database"])

        if query is None:
            query = ""
        if self.eval_result["query_type"] in ["dml", "ddl"]:
            self.db.execute(self.eval_result["setup_sql"])
            if self.eval_result["eval_query"] and len(self.eval_result["eval_query"]) > 0:
                query = query + " " + self.eval_result['eval_query'][0]
            result, error = self.db.execute(query, rollback=rollback)
            self.db.execute(self.eval_result["cleanup_sql"])
        else:
            result, error = self.db.execute(query, rollback=rollback)

        return result, error

    def run(self, work_config: str = None) -> dict:
        """Runs the work item.

        Args:
          work_config:

        Returns:

        """
        generated_result = None
        generated_error = None
        golden_result = None
        golden_error = None

        rollback = (self.eval_result["query_type"] == "dml")

        if (
            self.eval_result["sql_generator_error"] is None
            and self.eval_result["generated_sql"]
        ):
            self.eval_result["sanitized_sql"] = (
                self.eval_result["generated_sql"]
                .replace('sql: "', "")
                .replace("\\n", " ")
                .replace("\\n", " ")
                .replace("\\", "")
                .replace("  ", "")
                .replace("`", "")
            )

            generated_result, generated_error = self.execute_sql_flow(self.eval_result["sanitized_sql"],
                                                                      rollback=rollback)
            if self.eval_result["query_type"] == "ddl":
                generated_result = self.db.get_metadata()

            golden_sql = ""
            if isinstance(self.eval_result["golden_sql"], str):
                golden_sql = self.eval_result["golden_sql"]
            elif (
                isinstance(self.eval_result["golden_sql"], list)
                and len(self.eval_result["golden_sql"]) > 0
            ):
                golden_sql = self.eval_result["golden_sql"][0]

            golden_result, golden_error = self.execute_sql_flow(golden_sql, rollback=rollback)
            if self.eval_result["query_type"] == "ddl":
                golden_result = self.db.get_metadata()

        self.eval_result["generated_result"] = generated_result
        self.eval_result["generated_error"] = generated_error

        self.eval_result["golden_result"] = golden_result
        self.eval_result["golden_error"] = golden_error

        # Release the database
        if self.eval_result["query_type"] == "ddl" and self.db_queue is not None:
            self.db_queue.put(self.db)
        return self.eval_result
