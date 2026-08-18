import logging
import queue
import uuid
from typing import Any

import pandas as pd
from evalproto import eval_agent_pb2
from generators.models.agentic_reverse_proxy import AGENT_PROXY_QUEUES
from reporting.report import Reporter
from util.context import rpc_id_var

logger = logging.getLogger(__name__)


class RemoteArtifactReporter(Reporter):
    """Reporter delegating workspace dump and GCS upload across the reverse bidi stream."""

    def __init__(self, reporting_config: dict[str, Any] | None, job_id: str, run_time: Any):
        super().__init__(reporting_config, job_id, run_time)
        self.bucket = reporting_config.get("bucket", "sobi_dc_share") if reporting_config else "sobi_dc_share"
        self.path_prefix = reporting_config.get("path_prefix", "runs") if reporting_config else "runs"
        self.export_path = reporting_config.get("export_path", "/workspace") if reporting_config else "/workspace"
        default_excludes = [".venv", "node_modules", "skills"]
        self.exclude_patterns = reporting_config.get("exclude_patterns", default_excludes) if reporting_config else default_excludes
        logger.info("Initialized RemoteArtifactReporter: bucket=%s, prefix=%s", self.bucket, self.path_prefix)

    def store(self, results: pd.DataFrame, store_type: Any) -> None:
        type_name = getattr(store_type, "name", str(store_type))
        if type_name != "EVALS":
            return

        session_id = rpc_id_var.get()
        if session_id not in AGENT_PROXY_QUEUES:
            logger.warning("RemoteArtifactReporter: session_id %s not in AGENT_PROXY_QUEUES, skipping remote archival", session_id)
            return

        inboxes, out_queue = AGENT_PROXY_QUEUES[session_id]
        correlation_id = str(uuid.uuid4())
        inbox: queue.Queue[eval_agent_pb2.AgentStreamMessage] = queue.Queue()
        inboxes[correlation_id] = inbox

        artifact_req = eval_agent_pb2.ArtifactRequest(
            target_gcs_bucket=self.bucket,
            target_gcs_prefix=self.path_prefix,
            export_path=self.export_path,
            exclude_patterns=self.exclude_patterns,
        )

        msg = eval_agent_pb2.AgentStreamMessage(
            session_id=session_id,
            correlation_id=correlation_id,
            artifact_request=artifact_req,
        )

        logger.info("[REVERSE_REPORTER] Dispatching ArtifactRequest to out_queue (correlation_id=%s)", correlation_id)
        out_queue.put(msg)

        try:
            resp_msg = inbox.get(timeout=300.0)
            if resp_msg.HasField("artifact_response"):
                art_resp = resp_msg.artifact_response
                logger.info("[REVERSE_REPORTER] Remote workspace archived successfully to: %s (%d bytes)", art_resp.gcs_uri, art_resp.archive_size_bytes)
            else:
                logger.warning("[REVERSE_REPORTER] Received non-artifact response: %s", resp_msg.WhichOneof("payload"))
        except queue.Empty:
            logger.error("[REVERSE_REPORTER] Timed out waiting for ArtifactResponse")
        finally:
            inboxes.pop(correlation_id, None)
