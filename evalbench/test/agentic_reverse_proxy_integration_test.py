import asyncio
import json
import os
import shutil
import tempfile
import unittest
import uuid

import grpc
import yaml

from eval_service import EvalServicer, SessionManagerInterceptor
from evalproto import (
    eval_agent_pb2,
    eval_config_pb2,
    eval_connect_pb2,
    eval_request_pb2,
    eval_service_pb2_grpc,
)


class TestAgenticReverseProxyIntegration(unittest.IsolatedAsyncioTestCase):
    """Hermetic end-to-end integration test for the Agentic Reverse Proxy mechanism."""

    async def asyncSetUp(self):
        # 1. Setup temporary staging directory for configs and test dataset
        self.temp_dir = tempfile.mkdtemp()

        self.model_config_path = os.path.join(self.temp_dir, "model_config.yaml")
        with open(self.model_config_path, "w") as f:
            yaml.dump({"generator": "agentic_reverse_proxy", "timeout_seconds": 10.0}, f)

        self.simulated_user_config_path = os.path.join(self.temp_dir, "simulated_user.yaml")
        with open(self.simulated_user_config_path, "w") as f:
            yaml.dump({"generator": "noop"}, f)

        self.scenarios_path = os.path.join(self.temp_dir, "scenarios.json")
        scenario_data = {
            "id": "integration_evalset",
            "scenarios": [
                {
                    "id": "scenario_multiturn_01",
                    "starting_prompt": "Turn 1: Inspect files. Turn 2: Compile Dataform.",
                    "conversation_plan": [
                        "Turn 1: inspect files",
                        "Turn 2: compile",
                    ],
                    "expected_trajectory": [
                        "dataform__compile",
                    ],
                    "expected_skills": [
                        "dataform_best_practices",
                    ],
                    "binary_rubric": [
                        "Dataform pipeline compiled successfully",
                    ],
                    "max_turns": 2,
                }
            ],
        }
        with open(self.scenarios_path, "w") as f:
            json.dump(scenario_data, f)

        self.run_config = {
            "experiment_name": "test_agentic_reverse_proxy_integration",
            "orchestrator": "agent",
            "model_config": self.model_config_path,
            "simulated_user_model_config": self.simulated_user_config_path,
            "dataset_format": "agent-format",
            "dataset_config": self.scenarios_path,
            "scorers": {
                "trajectory_matcher": {
                    "enforce_order": True,
                },
                "skills_trajectory": {
                    "enforce_order": True,
                },
                "dataform_compile": {
                    "delegated": True,
                    "timeout_seconds": 10.0,
                },
            },
            "reporting": {
                "gcs_artifacts": {
                    "delegated": True,
                    "bucket": "test_bucket",
                    "path_prefix": "test_runs",
                },
            },
        }

        # 2. Start ephemeral in-process gRPC server on 127.0.0.1:0
        interceptors = [SessionManagerInterceptor("SessionManagerInterceptor")]
        self.server = grpc.aio.server(interceptors=interceptors)
        self.servicer = EvalServicer()
        eval_service_pb2_grpc.add_EvalServiceServicer_to_server(self.servicer, self.server)

        port = self.server.add_insecure_port("127.0.0.1:0")
        await self.server.start()

        # 3. Create client channel
        self.server_address = f"127.0.0.1:{port}"
        self.channel = grpc.aio.insecure_channel(self.server_address)
        self.stub = eval_service_pb2_grpc.EvalServiceStub(self.channel)

    async def asyncTearDown(self):
        await self.channel.close()
        await self.server.stop(grace=0)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def test_multi_turn_reverse_proxy_e2e(self):
        session_id = uuid.uuid4().hex
        metadata = [("client-rpc-id", session_id)]

        # 1. Ping
        ping_resp = await self.stub.Ping(eval_request_pb2.PingRequest(), metadata=metadata)
        self.assertEqual(ping_resp.response, "ack")

        # 2. Connect
        conn_req = eval_connect_pb2.EvalConnectRequest(bidirectional_stream=True)
        conn_resp = await self.stub.Connect(conn_req, metadata=metadata)
        self.assertEqual(conn_resp.response, "ack")

        # 3. EvalConfig
        yaml_bytes = yaml.dump(self.run_config).encode("utf-8")
        cfg_req = eval_config_pb2.EvalConfigRequest(yaml_config=yaml_bytes)
        cfg_resp = await self.stub.EvalConfig(cfg_req, metadata=metadata)
        self.assertEqual(cfg_resp.response, "ack")

        # 4. Open AgentInteract bidirectional stream
        bidi_call = self.stub.AgentInteract(metadata=metadata)
        send_queue = asyncio.Queue()

        async def sender():
            while True:
                msg = await send_queue.get()
                if msg is None:
                    break
                await bidi_call.write(msg)

        sender_task = asyncio.create_task(sender())
        summary_payload = None

        try:
            async for msg in bidi_call:
                payload_type = msg.WhichOneof("payload")
                corr_id = msg.correlation_id

                if payload_type == "turn_request":
                    turn_req = msg.turn_request

                    if turn_req.turn_index == 1:
                        # Turn 1: activate skill and run native command
                        t1 = eval_agent_pb2.ToolCallRecord(
                            tool_id="call_1",
                            tool_name="activate_skill",
                            parameters_json=json.dumps({"skill_name": "dataform_best_practices"}),
                            output="Skill activated",
                            status="success",
                            duration_ms=20,
                        )
                        t2 = eval_agent_pb2.ToolCallRecord(
                            tool_id="call_2",
                            tool_name="run_command",
                            parameters_json=json.dumps({"command": "ls definitions/"}),
                            output="definitions/test.sqlx",
                            status="success",
                            duration_ms=50,
                        )
                        turn_resp = eval_agent_pb2.TurnResponse(
                            turn_index=1,
                            response_text="Found pipeline files, preparing to compile.",
                            stdout="Found definitions",
                            exit_code=0,
                            tool_calls=[t1, t2],
                            token_stats={"input_tokens": 100, "output_tokens": 50},
                            execution_completed=False,
                        )
                    else:
                        # Turn 2: invoke MCP tool via Antigravity wrapper
                        t3 = eval_agent_pb2.ToolCallRecord(
                            tool_id="call_3",
                            tool_name="call_mcp_tool",
                            parameters_json=json.dumps({"ServerName": "dataform", "ToolName": "compile"}),
                            output="Compiled 1 action successfully",
                            status="success",
                            duration_ms=200,
                        )
                        turn_resp = eval_agent_pb2.TurnResponse(
                            turn_index=2,
                            response_text="Dataform compiled successfully.",
                            stdout="1 action compiled",
                            exit_code=0,
                            tool_calls=[t3],
                            token_stats={"input_tokens": 150, "output_tokens": 80},
                            execution_completed=True,
                        )

                    reply = eval_agent_pb2.AgentStreamMessage(
                        session_id=session_id,
                        correlation_id=corr_id,
                        turn_response=turn_resp,
                    )
                    await send_queue.put(reply)

                elif payload_type == "scoring_request":
                    spec = msg.scoring_request.scorer
                    self.assertEqual(spec.scorer_name, "dataform_compile")
                    score_res = eval_agent_pb2.ScoreResult(
                        scorer_name=spec.scorer_name,
                        score=100.0,
                        success=True,
                        exit_code=0,
                        stdout="Dataform compilation verified in sandbox",
                        logs="PASSED",
                    )
                    reply = eval_agent_pb2.AgentStreamMessage(
                        session_id=session_id,
                        correlation_id=corr_id,
                        scoring_response=eval_agent_pb2.ScoringResponse(result=score_res),
                    )
                    await send_queue.put(reply)

                elif payload_type == "reporting_request":
                    rep_spec = msg.reporting_request.reporter
                    self.assertEqual(rep_spec.reporter_name, "gcs_artifacts")
                    rep_res = eval_agent_pb2.ReporterResult(
                        reporter_name=rep_spec.reporter_name,
                        success=True,
                        result_json=json.dumps({"gcs_uri": "gs://test_bucket/test_runs/workspace.zip"}),
                    )
                    reply = eval_agent_pb2.AgentStreamMessage(
                        session_id=session_id,
                        correlation_id=corr_id,
                        reporting_response=eval_agent_pb2.ReportingResponse(result=rep_res),
                    )
                    await send_queue.put(reply)

                elif payload_type == "session_summary":
                    summary_payload = json.loads(msg.session_summary.summary_json)
                    break

        finally:
            await send_queue.put(None)
            await sender_task

        # Verify all scores in the evaluation summary
        self.assertIsNotNone(summary_payload)
        self.assertEqual(summary_payload["total"], 1)
        self.assertEqual(summary_payload["scores"]["trajectory_matcher"], 1)
        self.assertEqual(summary_payload["scores"]["skills_trajectory"], 1)
        self.assertEqual(summary_payload["scores"]["dataform_compile"], 1)


if __name__ == "__main__":
    unittest.main()
