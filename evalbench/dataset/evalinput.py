from google.protobuf.json_format import MessageToDict
import copy

try:
    from evalproto import eval_request_pb2

    PROTO_IMPORTED = True
except ImportError:
    PROTO_IMPORTED = False


class EvalInputRequest:
    def __init__(
        self,
        id: str,
        query_type: str,
        database: str,
        nl_prompt: str,
        dialects: list,
        golden_sql: dict | list,
        eval_query: dict | list,
        setup_sql: dict | list,
        cleanup_sql: dict | list,
        tags: list,
        other: dict,
        sql_generator_error: str = "",
        sql_generator_time: float = 0.0,
        generated_sql: str = "",
        job_id: str = "",
        trace_id: str = "",
        payload: str = "",
    ):
        """Initializes an EvalInputRequest object with all required fields.

        See eval_request_pb2 for types
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
        self.trace_id = trace_id
        self.payload = payload

    if PROTO_IMPORTED:

        @classmethod
        def init_from_proto(self, proto: eval_request_pb2.EvalInputRequest):  # type: ignore
            """Initializes an EvalInputRequest from eval_request_pb2 proto."""

            request = MessageToDict(proto)
            return self(
                id=str(request.get("id") or -1),
                query_type=request.get("queryType") or "",
                database=request.get("database") or "",
                nl_prompt=request.get("nlPrompt") or "",
                dialects=request.get("dialects") or [],
                golden_sql=_get_dialect_based_sql(request.get("goldenSql")),
                eval_query=_get_dialect_based_sql(request.get("evalQuery")),
                setup_sql=_get_dialect_based_sql(request.get("setupSql")),
                cleanup_sql=_get_dialect_based_sql(request.get("cleanupSql")),
                tags=request.get("tags") or [],
                other=request.get("other") or {},
                sql_generator_error=request.get("sqlGeneratorError") or "",
                sql_generator_time=request.get("sqlGeneratorTime") or 0,
                generated_sql=request.get("generatedSql") or "",
                job_id=request.get("jobId") or "",
                trace_id=request.get("traceId") or "",
                payload=request.get("payload") or "",
            )

        def to_proto(self):  # type: ignore
            return eval_request_pb2.EvalInputRequest(  # type: ignore
                id=int(self.id),
                query_type=self.query_type,
                database=self.database,
                nl_prompt=self.nl_prompt,
                dialects=self.dialects,
                golden_sql=self._set_dialect_based_sql(self.golden_sql),
                eval_query=self._set_dialect_based_sql(self.eval_query),
                setup_sql=self._set_dialect_based_sql(self.setup_sql),
                cleanup_sql=self._set_dialect_based_sql(self.cleanup_sql),
                tags=self.tags,
                other=self.other,
                sql_generator_error=self.sql_generator_error,
                sql_generator_time=self.sql_generator_time,
                generated_sql=self.generated_sql,
                job_id=self.job_id,
                trace_id=self.trace_id,
                payload=self.payload,
            )

        def _set_dialect_based_sql(self, dialect_based_sql):
            if not isinstance(dialect_based_sql, dict):
                return None
            # type: ignore
            response: dict[str,
                           eval_request_pb2.DialectBasedSQLStatements] = {}
            for dialect, sql_statements in dialect_based_sql.items():
                # type: ignore
                response[dialect] = eval_request_pb2.DialectBasedSQLStatements()
                if isinstance(sql_statements, list):
                    for sql_statement in sql_statements:
                        if isinstance(sql_statement, str):
                            response[dialect].sql_statements.append(
                                sql_statement)
            return response

    else:

        @classmethod
        def init_from_proto(cls, proto):
            raise ImportError("protobuf module not available")

        def to_proto(self):
            raise ImportError("protobuf module not available")

    def copy(self):
        return copy.deepcopy(self)

    def copy_for_dialect(self, dialect: str):
        copy = self.copy()
        copy.dialects = [dialect]
        if isinstance(self.golden_sql, dict):
            copy.golden_sql = self.golden_sql.get(dialect, [])
        if isinstance(self.eval_query, dict):
            copy.eval_query = self.eval_query.get(dialect, [])
        if isinstance(self.setup_sql, dict):
            copy.setup_sql = self.setup_sql.get(dialect, [])
        if isinstance(self.cleanup_sql, dict):
            copy.cleanup_sql = self.cleanup_sql.get(dialect, [])
        return copy


def _get_dialect_based_sql(dialect_based_sql):
    if not dialect_based_sql:
        return {}
    return {
        dialect: sqls["sqlStatements"] if "sqlStatements" in sqls else []
        for dialect, sqls in dialect_based_sql.items()
    }


def breakdown_datasets(total_dataset: list[EvalInputRequest]):
    """
    The shape of the output will be dict[str, dict[str, list[EvalInputRequest]]]
    in the following format:
    {
      dialect (str):
      -> database (str):
          -> query_type (str; [dql,dml,ddl]):
              -> list[EvalInputRequest]
    }
    """
    total_dataset_len = 0
    total_db_len = 0
    datasets: dict[str, dict[str, dict[str, list[EvalInputRequest]]]] = {}
    for input in total_dataset:
        for dialect in input.dialects:
            if dialect not in datasets:
                datasets[dialect] = {}
            if input.database not in datasets[dialect]:
                datasets[dialect][input.database] = {}
            if input.query_type not in datasets[dialect][input.database]:
                datasets[dialect][input.database][input.query_type] = []
                total_db_len += 1
            datasets[dialect][input.database][input.query_type].append(
                input.copy_for_dialect(dialect)
            )
            total_dataset_len += 1
    return datasets, total_dataset_len, total_db_len
