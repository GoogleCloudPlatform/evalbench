import json
import logging
import queue
import uuid
from typing import Any

from evalproto import eval_agent_pb2
from generators.models.agent_grpc_proxy import AGENT_GRPC_PROXY_QUEUES
from scorers.comparator import Comparator
from util.context import rpc_id_var


logger = logging.getLogger(__name__)


def _to_str(val: Any) -> str:
    """Defensively coerces evaluation arguments to strings for Protobuf packing.

    Note: While Comparator.compare hints string types, EvalBench runners
    do not strictly enforce strings at runtime. Specifically, agent evaluations
    (agentscorework.py) pass raw Python lists (e.g. tool calls in generated_result),
    dictionaries (eval_results), or None (generated_error). Because Google Protobuf
    string fields require str/bytes and raise a TypeError if passed list/dict/None,
    we defensively coerce them to strings (JSON-serialized for lists/dicts) before packing.
    """
    if val is None:
        return ""
    if isinstance(val, str):
        return val
    if isinstance(val, (dict, list)):
        return json.dumps(val)
    return str(val)


class RemoteScorerProxy(Comparator):
    """Comparator proxying scoring evaluation across the reverse bidi stream."""

    def __init__(self, name: str, config: dict[str, Any]):
        super().__init__(config or {})
        self.name = name
        self.config = dict(config) if isinstance(config, dict) else {}
        self.timeout_seconds = float(self.config.get("timeout_seconds", 300.0))
        logger.info(
            "Initialized RemoteScorerProxy: name=%s, config=%s",
            self.name,
            self.config,
        )

    def compare(
        self,
        nl_prompt: str,
        golden_query: str = "",
        query_type: str = "",
        golden_result: Any = "",
        golden_eval_results: Any = "",
        golden_error: Any = "",
        generated_query: str = "",
        generated_result: Any = "",
        eval_results: Any = "",
        generated_error: Any = "",
        database: str = "",
        **kwargs: Any,
    ) -> tuple[float, str] | list[tuple[str, float, str]]:
        session_id = rpc_id_var.get()
        if session_id not in AGENT_GRPC_PROXY_QUEUES:
            logger.error(
                "RemoteScorerProxy: session_id %s not in AGENT_GRPC_PROXY_QUEUES",
                session_id,
            )
            return (
                0.0,
                f"Error: session_id '{session_id}' not connected to stream",
            )

        inboxes, out_queue = AGENT_GRPC_PROXY_QUEUES[session_id]
        correlation_id = str(uuid.uuid4())
        inbox: queue.Queue[eval_agent_pb2.AgentStreamMessage] = queue.Queue()
        inboxes[correlation_id] = inbox

        scorer_spec = eval_agent_pb2.ScorerSpec(
            scorer_name=self.name,
            config_json=json.dumps(self.config),
            timeout_seconds=self.timeout_seconds,
        )

        golden_query = golden_query or kwargs.get("golden_sql", "")
        generated_query = generated_query or kwargs.get("generated_sql", "")
        database = database or kwargs.get("database", "")
        if golden_result in ("", None):
            golden_result = kwargs.get("golden_execution_result", "")
        if golden_eval_results in ("", None):
            golden_eval_results = kwargs.get("golden_eval_result", "")
        if generated_result in ("", None):
            generated_result = kwargs.get("generated_execution_result", "")
        if eval_results in ("", None):
            eval_results = kwargs.get("generated_eval_result", "")

        scoring_context = eval_agent_pb2.ScoringContext(
            nl_prompt=_to_str(nl_prompt),
            golden_query=_to_str(golden_query),
            query_type=_to_str(query_type),
            golden_result=_to_str(golden_result),
            golden_eval_results=_to_str(golden_eval_results),
            golden_error=_to_str(golden_error),
            generated_query=_to_str(generated_query),
            generated_result=_to_str(generated_result),
            eval_results=_to_str(eval_results),
            generated_error=_to_str(generated_error),
            database=_to_str(database),
        )

        scoring_req = eval_agent_pb2.ScoringRequest(
            scorer=scorer_spec,
            context=scoring_context,
        )
        msg = eval_agent_pb2.AgentStreamMessage(
            session_id=session_id,
            correlation_id=correlation_id,
            scoring_request=scoring_req,
        )

        logger.info(
            "[REMOTE_SCORER] Dispatching ScoringRequest for '%s' (correlation_id=%s)",
            self.name,
            correlation_id,
        )
        out_queue.put(msg)

        try:
            resp_msg = inbox.get(timeout=self.timeout_seconds)
        except queue.Empty:
            logger.error(
                "[REMOTE_SCORER] Timed out waiting for ScoringResponse for '%s' (correlation_id=%s)",
                self.name,
                correlation_id,
            )
            return (0.0, f"Error: Timed out waiting for remote scorer '{self.name}' response")
        finally:
            inboxes.pop(correlation_id, None)

        if not resp_msg.HasField("scoring_response"):
            err_details = resp_msg.WhichOneof("payload")
            logger.error("[REMOTE_SCORER] Unexpected message on stream: %s", err_details)
            return (0.0, f"Error: Unexpected payload on stream: {err_details}")

        scoring_resp = resp_msg.scoring_response
        result_type = scoring_resp.WhichOneof("result")

        if result_type == "single_score":
            s = scoring_resp.single_score
            return (float(s.score), s.comparison_logs)
        elif result_type == "multi_score":
            return [
                (
                    s.metric_name or self.name,
                    float(s.score),
                    s.comparison_logs,
                )
                for s in scoring_resp.multi_score.scores
            ]

        return (0.0, "Error: Empty score response from remote scorer")
