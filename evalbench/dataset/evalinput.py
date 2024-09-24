class EvalInputRequest:

    def __init__(
        self,
        id: str,
        query_type: str,
        database: str,
        nl_prompt: str,
        dialects: list,
        golden_sql: list,
        eval_query: list,
        setup_sql: list,
        cleanup_sql: list,
        tags: list,
        other: dict,
        sql_generator_error: str = "",
        sql_generator_time: float = 0.0,
        generated_sql: str = "",
        job_id: str = "",
    ):
        """Initializes an EvalInputRequest object with all required fields.

        :param id: A unique string identifier or name for this EvalInputRequest. Unique
        within a dataset file.
        :param query_type: The type of SQL query ("DDL" for Data Definition
        Language, "DML" for Data Manipulation Language,
                           or "DQL" for Data Query Language).
        :param database: The database for this dataset.
        :param nl_prompt: The human language question for which the
        model is to generate an SQL query.
        :param dialects: The list of dialects for which the model is to
        generate an SQL query.
        :param golden_query: The correct/expected SQL query for the given human
        language question.
        """
        self.id = id
        self.database = database
        self.query_type = query_type
        self.nl_prompt = nl_prompt
        self.dialects = dialects
        self.golden_sql = golden_sql
        self.eval_query = eval_query
        self.setup_sql = setup_sql
        self.cleanup_sql = cleanup_sql
        self.tags = tags
        self.other = other
        self.sql_generator_error = sql_generator_error
        self.sql_generator_time = sql_generator_time
        self.generated_sql = generated_sql
        self.job_id = job_id
