import queue
import unittest
from unittest.mock import MagicMock

from evalproto import eval_agent_pb2
from generators.models.agentic_reverse_proxy import (
    AgenticReverseProxyGenerator,
    AGENT_PROXY_QUEUES,
)
from reporting.remote_artifact_reporter import RemoteArtifactReporter
from scorers.remote_scorer import RemoteScorerProxy
from util.context import rpc_id_var


class TestAgenticReverseProxy(unittest.TestCase):

    def setUp(self):
        self.session_id = "test_session_123"
        self.inboxes = {}
        self.out_queue = queue.Queue()
        AGENT_PROXY_QUEUES[self.session_id] = (self.inboxes, self.out_queue)
        rpc_id_var.set(self.session_id)

    def tearDown(self):
        AGENT_PROXY_QUEUES.pop(self.session_id, None)

    def test_generator_turn_success(self):
        generator = AgenticReverseProxyGenerator({"timeout_seconds": 5.0})

        def answer():
            msg = self.out_queue.get(timeout=2.0)
            corr_id = msg.correlation_id
            turn_req = msg.turn_request
            self.assertEqual(turn_req.turn_index, 1)
            self.assertEqual(turn_req.prompt, "hello agent")

            t1 = eval_agent_pb2.ToolCallRecord(
                tool_id="call_1",
                tool_name="list_directory",
                parameters_json='{"path": "/workspace"}',
                output="files",
                status="success",
                duration_ms=50,
            )
            turn_resp = eval_agent_pb2.TurnResponse(
                turn_index=1,
                response_text="Found files",
                stdout="done",
                exit_code=0,
                tool_calls=[t1],
                token_stats={"input_tokens": 100},
                execution_completed=True,
            )
            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                turn_response=turn_resp,
            )
            self.inboxes[corr_id].put(reply)

        import threading
        t = threading.Thread(target=answer)
        t.start()

        cmd = generator.create_command("agent", "hello agent", session_id=self.session_id)
        res = generator.safe_generate(cmd)
        t.join()

        self.assertEqual(res.returncode, 0)
        parsed = generator.parse_response(res.stdout)
        self.assertEqual(parsed["response"], "Found files")
        self.assertEqual(len(parsed["tool_calls"]), 1)
        self.assertEqual(parsed["tool_calls"][0]["tool_name"], "list_directory")

    def test_remote_scorer_success(self):
        scorer = RemoteScorerProxy("dataform_compile", {"timeout_seconds": 5.0})

        def answer_scorer():
            msg = self.out_queue.get(timeout=2.0)
            corr_id = msg.correlation_id
            self.assertEqual(msg.WhichOneof("payload"), "scoring_request")
            spec = msg.scoring_request.scorers[0]
            self.assertEqual(spec.name, "dataform_compile")

            score_res = eval_agent_pb2.ScoreResult(
                name="dataform_compile",
                score=100.0,
                exit_code=0,
                stdout="Compiled 1 action",
                logs="Verified",
            )
            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                scoring_response=eval_agent_pb2.ScoringResponse(results=[score_res]),
            )
            self.inboxes[corr_id].put(reply)

        import threading
        t = threading.Thread(target=answer_scorer)
        t.start()

        score, logs = scorer.compare(
            nl_prompt="build pipeline",
            golden_sql="",
            query_type="",
            golden_result="",
            golden_eval_results="",
            golden_error="",
            generated_sql="",
            generated_result="",
            eval_results="",
            generated_error="",
        )
        t.join()

        self.assertEqual(score, 100.0)
        self.assertEqual(logs, "Verified")

    def test_remote_artifact_reporter_success(self):
        reporter = RemoteArtifactReporter(
            {"bucket": "test-bucket", "path_prefix": "runs", "export_path": "/workspace"},
            job_id="job_123",
            run_time="2026-08-18",
        )

        def answer_artifact():
            msg = self.out_queue.get(timeout=2.0)
            corr_id = msg.correlation_id
            self.assertEqual(msg.WhichOneof("payload"), "artifact_request")
            art_req = msg.artifact_request
            self.assertEqual(art_req.target_gcs_bucket, "test-bucket")

            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                artifact_response=eval_agent_pb2.ArtifactResponse(
                    gcs_uri="gs://test-bucket/runs/fake_home.zip",
                    archive_size_bytes=2048,
                    exported_files=["notes.txt"],
                ),
            )
            self.inboxes[corr_id].put(reply)

        import threading
        t = threading.Thread(target=answer_artifact)
        t.start()

        import pandas as pd
        from reporting.report import STORETYPE
        df = pd.DataFrame({"eval_id": ["1"]})
        reporter.store(df, STORETYPE.EVALS)
        t.join()


if __name__ == "__main__":
    unittest.main()
