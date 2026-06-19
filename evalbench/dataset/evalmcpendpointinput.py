"""Per-endpoint request wrapper for MCP compliance-check runs.

Mirrors EvalGeminiCliRequest: holds the raw payload (the endpoint config
JSON, serialized) that the orchestrator iterates over. The compliance
evaluator unpacks ``payload`` to discover the list of endpoints to probe.
"""

import copy


class EvalMcpEndpointRequest:
    def __init__(
        self,
        id: str,
        payload: str,
        job_id: str = "",
        trace_id: str = "",
    ):
        self.id = id
        self.payload = payload
        self.job_id = job_id
        self.trace_id = trace_id

    def copy(self):
        return copy.deepcopy(self)
