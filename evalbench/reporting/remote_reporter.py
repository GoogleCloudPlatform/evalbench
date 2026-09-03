import json
import logging
import queue
import uuid
from typing import Any

import pandas as pd
from evalproto import eval_agent_pb2
from generators.models.agent_grpc_proxy import AGENT_GRPC_PROXY_QUEUES
from reporting.report import Reporter
from util.context import rpc_id_var

logger = logging.getLogger(__name__)


class RemoteReporter(Reporter):
    """Generic reporter proxy delegating telemetry or artifact exports across the stream."""

    def __init__(
        self,
        reporter_name: str,
        reporting_config: dict[str, Any] | None,
        job_id: str,
        run_time: Any,
    ):
        super().__init__(reporting_config, job_id, run_time)
        self.reporter_name = reporter_name
        self.config = dict(reporting_config) if isinstance(reporting_config, dict) else {}
        self.timeout_seconds = float(self.config.get("timeout_seconds", 300.0))
        self._reported = False
        logger.info(
            "Initialized RemoteReporter: name=%s, config=%s",
            self.reporter_name,
            self.config,
        )

    def store(self, results: pd.DataFrame, store_type: Any) -> None:
        type_name = getattr(store_type, "name", str(store_type))
        # Delegate once during evaluation results processing
        if type_name != "EVALS":
            return
        if self._reported:
            return

        session_id = rpc_id_var.get()
        if session_id not in AGENT_GRPC_PROXY_QUEUES:
            logger.warning(
                "RemoteReporter: session_id %s not in AGENT_GRPC_PROXY_QUEUES, "
                "skipping delegated reporting",
                session_id,
            )
            return

        inboxes, out_queue = AGENT_GRPC_PROXY_QUEUES[session_id]
        correlation_id = str(uuid.uuid4())
        inbox: queue.Queue[eval_agent_pb2.AgentStreamMessage] = queue.Queue()
        inboxes[correlation_id] = inbox

        reporter_spec = eval_agent_pb2.ReporterSpec(
            reporter_name=self.reporter_name,
            config_json=json.dumps(self.config),
            timeout_seconds=self.timeout_seconds,
        )

        msg = eval_agent_pb2.AgentStreamMessage(
            session_id=session_id,
            correlation_id=correlation_id,
            reporting_request=eval_agent_pb2.ReportingRequest(reporter=reporter_spec),
        )

        logger.info(
            "[REVERSE_REPORTER] Dispatching ReportingRequest for '%s' (correlation_id=%s)",
            self.reporter_name,
            correlation_id,
        )
        out_queue.put(msg)

        try:
            resp_msg = inbox.get(timeout=self.timeout_seconds)
            if resp_msg.HasField("reporting_response"):
                res = resp_msg.reporting_response.result
                if res.success:
                    logger.info(
                        "[REVERSE_REPORTER] Delegated reporter '%s' completed successfully: %s",
                        self.reporter_name,
                        res.result_json,
                    )
                else:
                    logger.error(
                        "[REVERSE_REPORTER] Delegated reporter '%s' failed: %s",
                        self.reporter_name,
                        res.error_message,
                    )
                self._reported = True
            else:
                logger.warning(
                    "[REVERSE_REPORTER] Received unexpected response on stream: %s",
                    resp_msg.WhichOneof("payload"),
                )
        except queue.Empty:
            logger.error(
                "[REVERSE_REPORTER] Timed out waiting for ReportingResponse for '%s'",
                self.reporter_name,
            )
        finally:
            inboxes.pop(correlation_id, None)
