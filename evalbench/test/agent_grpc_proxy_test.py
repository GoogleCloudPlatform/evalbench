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
from scorers.remote_scorer import RemoteScorerProxy
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

            score_res = eval_agent_pb2.ScoreResult(
                scorer_name="dataform_compile",
                score=100.0,
                success=True,
                result_json=json.dumps({"status": "verified"}),
            )
            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                scoring_response=eval_agent_pb2.ScoringResponse(result=score_res),
            )
            self.inboxes[corr_id].put(reply)

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
        self.assertEqual(logs, json.dumps({"status": "verified"}))

    def test_remote_reporter_success(self):
        reporter = RemoteReporter(
            "gcs_artifacts",
            {"bucket": "test-bucket", "path_prefix": "runs", "database": "bigquery"},
            job_id="job_123",
            run_time="2026-08-18",
        )

        def answer_reporter():
            msg = self.out_queue.get(timeout=2.0)
            corr_id = msg.correlation_id
            self.assertEqual(msg.WhichOneof("payload"), "reporting_request")
            rep_spec = msg.reporting_request.reporter
            self.assertEqual(rep_spec.reporter_name, "gcs_artifacts")

            ctx = msg.reporting_request.context
            self.assertEqual(ctx.job_id, "job_123")
            self.assertEqual(ctx.run_time, "2026-08-18")
            self.assertEqual(ctx.store_type, "EVALS")
            self.assertEqual(ctx.database, "bigquery")
            parsed_data = json.loads(ctx.results_json)
            self.assertEqual(parsed_data, [{"eval_id": "1"}])

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

    def test_remote_reporter_all_store_types(self):
        reporter = RemoteReporter(
            "csv",
            {"output_directory": "/tmp/results"},
            job_id="job_456",
            run_time="2026-08-18",
        )

        dispatched_types = []

        def answer_reporter():
            for _ in range(4):
                msg = self.out_queue.get(timeout=2.0)
                corr_id = msg.correlation_id
                ctx = msg.reporting_request.context
                dispatched_types.append(ctx.store_type)

                reply = eval_agent_pb2.AgentStreamMessage(
                    session_id=self.session_id,
                    correlation_id=corr_id,
                    reporting_response=eval_agent_pb2.ReportingResponse(
                        result=eval_agent_pb2.ReporterResult(
                            reporter_name="csv",
                            success=True,
                            result_json=json.dumps({"written": ctx.store_type}),
                        )
                    ),
                )
                self.inboxes[corr_id].put(reply)

        t = threading.Thread(target=answer_reporter)
        t.start()

        df = pd.DataFrame({"key": ["val"]})
        reporter.store(df, STORETYPE.CONFIGS)
        reporter.store(df, STORETYPE.EVALS)
        reporter.store(df, STORETYPE.SCORES)
        reporter.store(df, STORETYPE.SUMMARY)
        t.join()

        self.assertEqual(
            dispatched_types,
            ["CONFIGS", "EVALS", "SCORES", "SUMMARY"],
        )

    def test_remote_reporter_timeout(self):
        reporter = RemoteReporter(
            "slow_reporter",
            {"timeout_seconds": 0.1},
            job_id="job_slow",
            run_time="2026-08-18",
        )
        df = pd.DataFrame({"eval_id": ["1"]})
        # Should complete without raising exception
        reporter.store(df, STORETYPE.EVALS)

    def test_remote_reporter_failure_response(self):
        reporter = RemoteReporter(
            "failing_reporter",
            {"timeout_seconds": 5.0},
            job_id="job_fail",
            run_time="2026-08-18",
        )

        def answer_failure():
            msg = self.out_queue.get(timeout=2.0)
            corr_id = msg.correlation_id

            reply = eval_agent_pb2.AgentStreamMessage(
                session_id=self.session_id,
                correlation_id=corr_id,
                reporting_response=eval_agent_pb2.ReportingResponse(
                    result=eval_agent_pb2.ReporterResult(
                        reporter_name="failing_reporter",
                        success=False,
                        error_message="GCS bucket not accessible",
                    )
                ),
            )
            self.inboxes[corr_id].put(reply)

        t = threading.Thread(target=answer_failure)
        t.start()

        df = pd.DataFrame({"eval_id": ["1"]})
        reporter.store(df, STORETYPE.EVALS)
        t.join()


if __name__ == "__main__":
    unittest.main()
