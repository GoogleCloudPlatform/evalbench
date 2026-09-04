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

        database = kwargs.get("database", "")
        scenario_id = ""
        if isinstance(eval_results, dict):
            scenario_id = eval_results.get("scenario", {}).get(
                "id", ""
            ) or eval_results.get("eval_id", "")
        elif eval_results and isinstance(eval_results, str):
            try:
                parsed_eval = json.loads(eval_results)
                if isinstance(parsed_eval, dict):
                    scenario_id = parsed_eval.get("scenario", {}).get(
                        "id", ""
                    ) or parsed_eval.get("eval_id", "")
            except Exception:
                pass

        scoring_context = eval_agent_pb2.ScoringContext(
            nl_prompt=str(nl_prompt or ""),
            golden_query=str(golden_sql or ""),
            query_type=str(query_type or ""),
            golden_result_json=(
                json.dumps(golden_result, default=str)
                if not isinstance(golden_result, str)
                else golden_result
            ),
            golden_eval_results=str(golden_eval_results or ""),
            golden_error=str(golden_error or ""),
            generated_query=str(generated_sql or ""),
            generated_result_json=(
                json.dumps(generated_result, default=str)
                if not isinstance(generated_result, str)
                else generated_result
            ),
            eval_results_json=(
                json.dumps(eval_results, default=str)
                if isinstance(eval_results, dict)
                else str(eval_results or "")
            ),
            generated_error=str(generated_error or ""),
            database=str(database or ""),
            scenario_id=str(scenario_id or ""),
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
            "[REVERSE_SCORER] Dispatching ScoringRequest for '%s' (correlation_id=%s, scenario=%s)",
            self.name,
            correlation_id,
            scenario_id,
        )
        out_queue.put(msg)

        try:
            resp_msg = inbox.get(timeout=self.timeout_seconds)
        except queue.Empty:
            logger.error(
                "[REVERSE_SCORER] Timed out waiting for ScoringResponse for '%s' (correlation_id=%s)",
                self.name,
                correlation_id,
            )
            return (0.0, f"Error: Timed out waiting for remote scorer '{self.name}' response")
        finally:
            inboxes.pop(correlation_id, None)

        if not resp_msg.HasField("scoring_response"):
            err_details = resp_msg.WhichOneof("payload")
            logger.error("[REVERSE_SCORER] Unexpected message on stream: %s", err_details)
            return (0.0, f"Error: Unexpected payload on stream: {err_details}")

        scores = resp_msg.scoring_response.scores
        if not scores:
            return (0.0, "Error: Empty score response from remote scorer")

        if len(scores) == 1:
            s = scores[0]
            log_output = (
                s.comparison_logs
                or s.error_message
                or ("PASSED" if s.success else "FAILED")
            )
            return (float(s.score), log_output)

        return [
            (
                s.metric_name or self.name,
                float(s.score),
                s.comparison_logs
                or s.error_message
                or ("PASSED" if s.success else "FAILED"),
            )
            for s in scores
        ]
