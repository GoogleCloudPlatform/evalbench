import json
import queue
import threading
import unittest

import pandas as pd
from evalproto import eval_agent_pb2
from generators.models.agent_grpc_proxy import (
    AGENT_GRPC_PROXY_QUEUES,
    AgentGrpcProxyGenerator,
)
from reporting.remote_reporter import RemoteReporter
from reporting.report import STORETYPE
from scorers.remote_scorer import RemoteScorerProxy, _to_str
from util.context import rpc_id_var


class TestAgentGrpcProxy(unittest.TestCase):

    def setUp(self):
        self.session_id = "test_session_123"
        self.inboxes = {}
        self.out_queue = queue.Queue()
        AGENT_GRPC_PROXY_QUEUES[self.session_id] = (self.inboxes, self.out_queue)
        rpc_id_var.set(self.session_id)

    def tearDown(self):
        AGENT_GRPC_PROXY_QUEUES.pop(self.session_id, None)

    def test_generator_turn_success(self):
        generator = AgentGrpcProxyGenerator({"timeout_seconds": 5.0})

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
                tool_calls=[t1],
                token_stats={"input_tokens": 100},
                success=True,
                execution_completed=True,
            )
            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                turn_response=turn_resp,
            )
            self.inboxes[corr_id].put(reply)

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
            spec = msg.scoring_request.scorer
            self.assertEqual(spec.scorer_name, "dataform_compile")

            # Verify ScoringContext fields were passed directly as strings
            ctx = msg.scoring_request.context
            self.assertEqual(ctx.nl_prompt, "build pipeline")
            self.assertEqual(ctx.database, "bigquery")
            self.assertEqual(ctx.golden_result, "golden_output")
            self.assertEqual(ctx.generated_result, "generated_output")
            self.assertEqual(ctx.eval_results, "step1_passed")

            single = eval_agent_pb2.SingleScore(
                score=100.0,
                comparison_logs="Compilation successful",
            )
            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                scoring_response=eval_agent_pb2.ScoringResponse(single_score=single),
            )
            self.inboxes[corr_id].put(reply)

        t = threading.Thread(target=answer_scorer)
        t.start()

        score, logs = scorer.compare(
            nl_prompt="build pipeline",
            golden_sql="",
            query_type="",
            golden_result="golden_output",
            golden_eval_results="",
            golden_error="",
            generated_sql="",
            generated_result="generated_output",
            eval_results="step1_passed",
            generated_error="",
            database="bigquery",
        )
        t.join()

        self.assertEqual(score, 100.0)
        self.assertEqual(logs, "Compilation successful")

    def test_remote_scorer_sub_scores(self):
        scorer = RemoteScorerProxy("multi_aspect_scorer", {"timeout_seconds": 5.0})

        def answer_multi_scorer():
            msg = self.out_queue.get(timeout=2.0)
            corr_id = msg.correlation_id
            self.assertEqual(msg.WhichOneof("payload"), "scoring_request")

            scores_list = [
                eval_agent_pb2.MetricScore(
                    metric_name="syntax", score=100.0, comparison_logs="Clean syntax"
                ),
                eval_agent_pb2.MetricScore(
                    metric_name="efficiency", score=80.0, comparison_logs="2 turns"
                ),
            ]
            multi = eval_agent_pb2.MultiScore(scores=scores_list)
            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                scoring_response=eval_agent_pb2.ScoringResponse(multi_score=multi),
            )
            self.inboxes[corr_id].put(reply)

        t = threading.Thread(target=answer_multi_scorer)
        t.start()

        result = scorer.compare(
            nl_prompt="test",
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

        self.assertIsInstance(result, list)
        self.assertEqual(len(result), 2)
        self.assertEqual(result[0], ("syntax", 100.0, "Clean syntax"))
        self.assertEqual(result[1], ("efficiency", 80.0, "2 turns"))

    def test_remote_scorer_timeout(self):
        scorer = RemoteScorerProxy("slow_scorer", {"timeout_seconds": 0.1})
        score, logs = scorer.compare(
            nl_prompt="test",
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
        self.assertEqual(score, 0.0)
        self.assertIn("Timed out waiting for remote scorer", logs)

    def test_remote_scorer_runtime_type_coercion(self):
        scorer = RemoteScorerProxy("rubric_scorer", {"timeout_seconds": 5.0})

        def answer_scorer():
            msg = self.out_queue.get(timeout=2.0)
            corr_id = msg.correlation_id
            self.assertEqual(msg.WhichOneof("payload"), "scoring_request")

            ctx = msg.scoring_request.context
            # Non-string runtime types (list, dict) should be coerced to JSON strings
            self.assertEqual(
                ctx.generated_result,
                json.dumps(["activate_skill", "run_command"]),
            )
            self.assertEqual(
                ctx.eval_results,
                json.dumps({"turn": 2, "tokens": 120}),
            )
            # None should be mapped to empty string
            self.assertEqual(ctx.generated_error, "")

            single = eval_agent_pb2.SingleScore(
                score=100.0,
                comparison_logs="Trajectory verified",
            )
            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                scoring_response=eval_agent_pb2.ScoringResponse(single_score=single),
            )
            self.inboxes[corr_id].put(reply)

        t = threading.Thread(target=answer_scorer)
        t.start()

        score, logs = scorer.compare(
            nl_prompt="verify skills",
            golden_query="",
            query_type="",
            golden_result="",
            golden_eval_results="",
            golden_error="",
            generated_query="",
            generated_result=["activate_skill", "run_command"],
            eval_results={"turn": 2, "tokens": 120},
            generated_error=None,
            database="bigquery",
        )
        t.join()

        self.assertEqual(score, 100.0)
        self.assertEqual(logs, "Trajectory verified")

    def test_to_str_helper(self):
        self.assertEqual(_to_str(None), "")
        self.assertEqual(_to_str("already_a_string"), "already_a_string")
        self.assertEqual(_to_str(["tool_a", "tool_b"]), '["tool_a", "tool_b"]')
        self.assertEqual(_to_str({"key": "val"}), '{"key": "val"}')
        self.assertEqual(_to_str(123), "123")

    def test_remote_reporter_success(self):
        reporter = RemoteReporter(
            "gcs_artifacts",
            {"bucket": "test-bucket", "path_prefix": "runs"},
            job_id="job_123",
            run_time="2026-08-18",
        )

        def answer_reporter():
            msg = self.out_queue.get(timeout=2.0)
            corr_id = msg.correlation_id
            self.assertEqual(msg.WhichOneof("payload"), "reporting_request")
            rep_spec = msg.reporting_request.reporter
            self.assertEqual(rep_spec.reporter_name, "gcs_artifacts")

            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                reporting_response=eval_agent_pb2.ReportingResponse(
                    result=eval_agent_pb2.ReporterResult(
                        reporter_name="gcs_artifacts",
                        success=True,
                        result_json='{"uri": "gs://test-bucket/runs/archive.zip"}',
                    )
                ),
            )
            self.inboxes[corr_id].put(reply)

        t = threading.Thread(target=answer_reporter)
        t.start()

        df = pd.DataFrame({"eval_id": ["1"]})
        reporter.store(df, STORETYPE.EVALS)
        t.join()
        self.assertTrue(reporter._reported)


if __name__ == "__main__":
    unittest.main()
