from google.protobuf.json_format import MessageToDict

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
        trace_id: str = "",
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

    if PROTO_IMPORTED:
        @classmethod
        def init_from_proto(self, proto: eval_request_pb2.EvalInputRequest):
            """Initializes an EvalInputRequest from eval_request_pb2 proto."""

            request = MessageToDict(proto)
            return self(
                id=request.get("id"),
                query_type=request.get("queryType"),
                database=request.get("database"),
                nl_prompt=request.get("nlPrompt"),
                dialects=request.get("dialects"),
                golden_sql=request.get("goldenSql"),
                eval_query=request.get("evalQuery"),
                setup_sql=request.get("setupSql"),
                cleanup_sql=request.get("cleanupSql"),
                tags=request.get("tags"),
                other=request.get("other"),
                sql_generator_error=request.get("sqlGeneratorError"),
                sql_generator_time=request.get("sqlGeneratorTime"),
                generated_sql=request.get("generatedSql"),
                job_id=request.get("jobId"),
                trace_id=request.get('traceId'),
            )
    else:
        @classmethod
        def init_from_proto(cls, proto):
            raise ImportError("protobuf module not available")
