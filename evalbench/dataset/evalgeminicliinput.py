from google.protobuf.json_format import MessageToDict
import copy
from enum import Enum

try:
    from evalproto import eval_request_pb2

    PROTO_IMPORTED = True
except ImportError:
    PROTO_IMPORTED = False


class EvalGeminiCliRequest:
    def __init__(
        self,
        id: str,
        payload: dict,
        job_id: str = "",
        trace_id: str = "",
    ):
        """Initializes an EvalGeminiCliRequest object with all required fields.

        See eval_request_pb2 for types
        """
        self.id = id
        self.payload = payload
        self.job_id = job_id
        self.trace_id = trace_id

    if PROTO_IMPORTED:

        @classmethod
        def init_from_proto(self, proto: eval_request_pb2.EvalInputRequest):  # type: ignore
            """Initializes an EvalGeminiCliRequest from eval_request_pb2 proto."""

            request = MessageToDict(proto)
            return self(
                id=str(request.get("id") or -1),
                payload=request.get("payload") or {},
                job_id=request.get("jobId") or "",
                trace_id=request.get("traceId") or "",
            )

        def to_proto(self):  # type: ignore
            return eval_request_pb2.EvalInputRequest(  # type: ignore
                id=int(self.id),
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
