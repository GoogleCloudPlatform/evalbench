from google.protobuf.json_format import MessageToDict
import copy
from enum import Enum

try:
    from evalproto import eval_request_pb2

    PROTO_IMPORTED = True
except ImportError:
    PROTO_IMPORTED = False


class InteractionType(Enum):
    INIT = 0
    LLM_QUESTION_PROMPT = 1
    LLM_SQLGEN = 2
    DISAMBIGUATE = 3
    VUSER_ENCODE = 4
    VUSER_DECODE = 5
    SQL_EXEC = 6
    SCORE = 7
    COMPLETE = 8


class EvalInteractInputRequest:
    def __init__(
        self,
        id: str,
        amb_user_query: str,
        query_type: str,
        database: str,
        dialects: list,
        eval_query: dict | list,
        tags: list,
        payload: dict,
        job_id: str = "",
        trace_id: str = "",
    ):
        """Initializes an EvalInputRequest object with all required fields.

        See eval_request_pb2 for types
        """
        self.id = id
        self.amb_user_query = amb_user_query
        self.query_type = query_type
        self.database = database
        self.dialects = dialects
        self.eval_query = eval_query
        self.tags = tags
        self.payload = payload
        self.job_id = job_id
        self.trace_id = trace_id

    if PROTO_IMPORTED:

        @classmethod
        def init_from_proto(self, proto: eval_request_pb2.EvalInputRequest):  # type: ignore
            """Initializes an EvalInputRequest from eval_request_pb2 proto."""

            request = MessageToDict(proto)
            return self(
                id=str(request.get("id") or -1),
                query_type=request.get("queryType") or "",
                database=request.get("database") or "",
                amb_user_query=request.get("amb_user_query") or "",
                dialects=request.get("dialects") or [],
                tags=request.get("tags") or [],
                payload=request.get("payload") or {},
                job_id=request.get("jobId") or "",
                trace_id=request.get("traceId") or "",
            )

        def to_proto(self):  # type: ignore
            return eval_request_pb2.EvalInputRequest(  # type: ignore
                id=int(self.id),
                query_type=self.query_type,
                database=self.database,
                amb_user_query=self.amb_user_query,
                dialects=self.dialects,
                tags=self.tags,
                payload=self.payload,
            )

    else:

        @classmethod
        def init_from_proto(cls, proto):
            raise ImportError("protobuf module not available")

        def to_proto(self):
            raise ImportError("protobuf module not available")

    def copy(self):
        return copy.deepcopy(self)


def breakdown_datasets(total_dataset: list[EvalInteractInputRequest]):
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
    datasets: dict[str,
                   dict[str, dict[str, list[EvalInteractInputRequest]]]] = {}
    for input in total_dataset:
        for dialect in input.dialects:
            if dialect not in datasets:
                datasets[dialect] = {}
            if input.database not in datasets[dialect]:
                datasets[dialect][input.database] = {}
            if input.query_type not in datasets[dialect][input.database]:
                datasets[dialect][input.database][input.query_type] = []
            datasets[dialect][input.database][input.query_type].append(
                input.copy())
        total_dataset_len += 1
    return datasets, total_dataset_len, total_db_len
