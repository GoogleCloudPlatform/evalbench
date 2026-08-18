import json
import logging
import queue
import uuid
from typing import Any, Dict, List, Tuple

from evalproto import eval_agent_pb2
from scorers.comparator import Comparator
from util.context import rpc_id_var
from generators.models.agentic_reverse_proxy import AGENT_PROXY_QUEUES


class RemoteScorerProxy(Comparator):
    """Comparator proxying scoring evaluation across the reverse bidi stream."""

    def __init__(self, name: str, config: Dict[str, Any]):
        super().__init__(config or {})
        self.name = name
        self.config = dict(config) if isinstance(config, dict) else {}
        self.timeout_seconds = float(self.config.get("timeout_seconds", 300.0))
        logging.info("Initialized RemoteScorerProxy: name=%s, config=%s", self.name, self.config)

    def compare(
        self,
        nl_prompt: str,
        golden_sql: str,
        query_type: str,
        golden_result: str,
        golden_eval_results: str,
        golden_error: str,
        generated_sql: str,
        generated_result: str,
        eval_results: str,
        generated_error: str,
        **kwargs: Any,
    ) -> Tuple[float, str] | List[Tuple[str, float, str]]:
        session_id = rpc_id_var.get()
        if session_id not in AGENT_PROXY_QUEUES:
            logging.error("RemoteScorerProxy: session_id %s not found in AGENT_PROXY_QUEUES", session_id)
            return (0.0, f"Error: session_id '{session_id}' not connected to reverse stream")

        inboxes, out_queue = AGENT_PROXY_QUEUES[session_id]
        correlation_id = str(uuid.uuid4())
        inbox: queue.Queue[eval_agent_pb2.AgentStreamMessage] = queue.Queue()
        inboxes[correlation_id] = inbox

        scorer_spec = eval_agent_pb2.RemoteScorerSpec(
            name=self.name,
            config_json=json.dumps(self.config),
            timeout_seconds=self.timeout_seconds,
        )

        scoring_req = eval_agent_pb2.ScoringRequest(scorers=[scorer_spec])
        msg = eval_agent_pb2.AgentStreamMessage(
            session_id=session_id,
            correlation_id=correlation_id,
            scoring_request=scoring_req,
        )

        logging.info("[REVERSE_SCORER] Dispatching ScoringRequest for '%s' (correlation_id=%s)", self.name, correlation_id)
        out_queue.put(msg)

        try:
            resp_msg = inbox.get(timeout=self.timeout_seconds)
        except queue.Empty:
            logging.error("[REVERSE_SCORER] Timed out waiting for ScoringResponse for '%s' (correlation_id=%s)", self.name, correlation_id)
            return (0.0, f"Error: Timed out waiting for remote scorer '{self.name}' response")
        finally:
            inboxes.pop(correlation_id, None)

        if not resp_msg.HasField("scoring_response"):
            err_details = resp_msg.WhichOneof("payload")
            logging.error("[REVERSE_SCORER] Unexpected message on stream: %s", err_details)
            return (0.0, f"Error: Unexpected payload on stream: {err_details}")

        scoring_resp = resp_msg.scoring_response
        results: List[Tuple[str, float, str]] = []
        for r in scoring_resp.results:
            log_output = r.logs or r.stdout or r.error_message or f"exit_code={r.exit_code}"
            results.append((r.name, float(r.score), log_output))

        if len(results) == 1 and results[0][0] == self.name:
            return (results[0][1], results[0][2])
        elif results:
            return results
        return (0.0, f"Error: Remote scorer '{self.name}' returned empty results")
