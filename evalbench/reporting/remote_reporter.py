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
        self.database = str(self.config.get("database", ""))
        self.artifact_uris: list[str] = []
        logger.info(
            "Initialized RemoteReporter: name=%s, config=%s",
            self.reporter_name,
            self.config,
        )

    def store(self, results: pd.DataFrame, store_type: Any) -> None:
        type_name = getattr(store_type, "name", str(store_type))

        session_id = rpc_id_var.get()
        if session_id not in AGENT_GRPC_PROXY_QUEUES:
            logger.warning(
                "RemoteReporter: session_id %s not in AGENT_GRPC_PROXY_QUEUES, "
                "skipping delegated reporting for %s",
                session_id,
                type_name,
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

        results_json = ""
        if isinstance(results, pd.DataFrame) and not results.empty:
            try:
                results_json = results.to_json(orient="records", date_format="iso")
            except Exception as e:
                logger.warning(
                    "RemoteReporter: Failed to serialize DataFrame to JSON: %s",
                    e,
                )
                results_json = ""

        reporting_context = eval_agent_pb2.ReportingContext(
            job_id=str(self.job_id or ""),
            run_time=str(self.run_time or ""),
            store_type=type_name,
            results_json=results_json,
            database=self.database,
        )

        msg = eval_agent_pb2.AgentStreamMessage(
            session_id=session_id,
            correlation_id=correlation_id,
            reporting_request=eval_agent_pb2.ReportingRequest(
                reporter=reporter_spec,
                context=reporting_context,
            ),
        )

        logger.info(
            "[REMOTE_REPORTER] Dispatching ReportingRequest for '%s' type=%s (correlation_id=%s)",
            self.reporter_name,
            type_name,
            correlation_id,
        )
        out_queue.put(msg)

        try:
            resp_msg = inbox.get(timeout=self.timeout_seconds)
            if resp_msg.HasField("reporting_response"):
                res = resp_msg.reporting_response.result
                if res.success:
                    logger.info(
                        "[REMOTE_REPORTER] Delegated reporter '%s' (%s) completed successfully: %s",
                        self.reporter_name,
                        type_name,
                        res.result_json,
                    )
                    if res.artifact_uris:
                        self.artifact_uris.extend(res.artifact_uris)
                else:
                    logger.error(
                        "[REMOTE_REPORTER] Delegated reporter '%s' (%s) failed: %s",
                        self.reporter_name,
                        type_name,
                        res.error_message,
                    )
            else:
                logger.warning(
                    "[REMOTE_REPORTER] Received unexpected response on stream: %s",
                    resp_msg.WhichOneof("payload"),
                )
        except queue.Empty:
            logger.error(
                "[REMOTE_REPORTER] Timed out waiting for ReportingResponse for '%s' (%s)",
                self.reporter_name,
                type_name,
            )
        finally:
            inboxes.pop(correlation_id, None)

    def print_dashboard_links(self) -> None:
        # TODO: Surface remote artifact URIs and dashboard links once UX format is finalized.
        pass
