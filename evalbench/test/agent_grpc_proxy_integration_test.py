import asyncio
import json
import os
import shutil
import sys
import tempfile
import unittest
import uuid

import grpc
import yaml

# protoc generates flat imports in eval_service_pb2_grpc (e.g. import
# eval_agent_pb2). Ensure evalproto is in sys.path so stubs resolve cleanly.
_PROTO_DIR = os.path.join(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "evalproto"
)
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

from eval_service import EvalServicer, SessionManagerInterceptor
from evalproto import (
    eval_agent_pb2,
    eval_config_pb2,
    eval_connect_pb2,
    eval_request_pb2,
    eval_service_pb2_grpc,
)
from util import get_SessionManager


class TestAgentGrpcProxyIntegration(unittest.IsolatedAsyncioTestCase):
    """Hermetic end-to-end integration test for the Agent gRPC Proxy mechanism,
    including multi-turn evaluations, concurrent sessions, and concurrent scenario multiplexing."""

    async def asyncSetUp(self):
        # 1. Setup temporary staging directory for configs and test dataset
        self.temp_dir = tempfile.mkdtemp()

        self.model_config_path = os.path.join(self.temp_dir, "model_config.yaml")
        with open(self.model_config_path, "w") as f:
            yaml.dump({"generator": "agent_grpc_proxy", "timeout_seconds": 10.0}, f)

        self.simulated_user_config_path = os.path.join(self.temp_dir, "simulated_user.yaml")
        with open(self.simulated_user_config_path, "w") as f:
            yaml.dump({"generator": "noop"}, f)

        # Single-scenario multi-turn dataset
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

        # Multi-scenario dataset for concurrency multiplexing
        self.multi_scenarios_path = os.path.join(self.temp_dir, "multi_scenarios.json")
        multi_scenario_data = {
            "id": "integration_multi_evalset",
            "scenarios": [
                {
                    "id": "scenario_concurrent_dataform",
                    "starting_prompt": "Task for dataform: compile pipeline",
                    "expected_trajectory": ["dataform__compile"],
                    "expected_skills": ["dataform_best_practices"],
                    "max_turns": 1,
                },
                {
                    "id": "scenario_concurrent_dbt",
                    "starting_prompt": "Task for dbt: compile models",
                    "expected_trajectory": ["dbt__compile"],
                    "expected_skills": ["dbt_best_practices"],
                    "max_turns": 1,
                },
                {
                    "id": "scenario_concurrent_bigquery",
                    "starting_prompt": "Task for bigquery: execute query",
                    "expected_trajectory": ["bigquery__execute_query"],
                    "expected_skills": ["sql_optimization"],
                    "max_turns": 1,
                },
            ],
        }
        with open(self.multi_scenarios_path, "w") as f:
            json.dump(multi_scenario_data, f)

        self.run_config = {
            "experiment_name": "test_agent_grpc_proxy_integration",
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

        self.multi_scenario_run_config = {
            "experiment_name": "test_multi_scenario_concurrency",
            "orchestrator": "agent",
            "model_config": self.model_config_path,
            "simulated_user_model_config": self.simulated_user_config_path,
            "dataset_format": "agent-format",
            "dataset_config": self.multi_scenarios_path,
            "runners": {
                "agent_runners": 5,
            },
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
        session_mgr = get_SessionManager()
        for sid in list(session_mgr.get_sessions().keys()):
            session_mgr.delete_session(sid)
        shutil.rmtree(self.temp_dir, ignore_errors=True)

    async def _run_client_session(self, session_id: str, run_config: dict, skill_name: str = "dataform_best_practices"):
        metadata = [("client-rpc-id", session_id)]

        # 1. Ping
        ping_resp = await self.stub.Ping(eval_request_pb2.PingRequest(), metadata=metadata)
        self.assertEqual(ping_resp.response, "ack")

        # 2. Connect
        conn_req = eval_connect_pb2.EvalConnectRequest(bidirectional_stream=True)
        conn_resp = await self.stub.Connect(conn_req, metadata=metadata)
        self.assertEqual(conn_resp.response, "ack")

        # 3. EvalConfig
        yaml_bytes = yaml.dump(run_config).encode("utf-8")
        cfg_req = eval_config_pb2.EvalConfigRequest(yaml_config=yaml_bytes)
        cfg_resp = await self.stub.EvalConfig(cfg_req, metadata=metadata)
        self.assertEqual(cfg_resp.response, "ack")

        # 4. Open AgentInteract stream
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
                        t1 = eval_agent_pb2.ToolCallRecord(
                            tool_id=f"call_{uuid.uuid4().hex[:6]}",
                            tool_name="activate_skill",
                            parameters_json=json.dumps({"skill_name": skill_name}),
                            output="Skill activated",
                            status="success",
                            duration_ms=20,
                        )
                        t2 = eval_agent_pb2.ToolCallRecord(
                            tool_id=f"call_{uuid.uuid4().hex[:6]}",
                            tool_name="run_command",
                            parameters_json=json.dumps({"command": "ls definitions/"}),
                            output="definitions/test.sqlx",
                            status="success",
                            duration_ms=50,
                        )
                        turn_resp = eval_agent_pb2.TurnResponse(
                            turn_index=1,
                            response_text="Found pipeline files, preparing to compile.",
                            tool_calls=[t1, t2],
                            token_stats={"input_tokens": 100, "output_tokens": 50},
                            success=True,
                            execution_completed=False,
                        )
                    else:
                        t3 = eval_agent_pb2.ToolCallRecord(
                            tool_id=f"call_{uuid.uuid4().hex[:6]}",
                            tool_name="dataform__compile",
                            parameters_json=json.dumps({"action": "compile"}),
                            output="Compiled 1 action successfully",
                            status="success",
                            duration_ms=200,
                        )
                        turn_resp = eval_agent_pb2.TurnResponse(
                            turn_index=2,
                            response_text="Dataform compiled successfully.",
                            tool_calls=[t3],
                            token_stats={"input_tokens": 150, "output_tokens": 80},
                            success=True,
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
                    metric = eval_agent_pb2.MetricScore(
                        metric_name=spec.scorer_name,
                        score=100.0,
                        comparison_logs=f"{spec.scorer_name} verified in sandbox",
                        success=True,
                    )
                    reply = eval_agent_pb2.AgentStreamMessage(
                        session_id=session_id,
                        correlation_id=corr_id,
                        scoring_response=eval_agent_pb2.ScoringResponse(scores=[metric]),
                    )
                    await send_queue.put(reply)

                elif payload_type == "reporting_request":
                    rep_spec = msg.reporting_request.reporter
                    rep_res = eval_agent_pb2.ReporterResult(
                        reporter_name=rep_spec.reporter_name,
                        success=True,
                        result_json=json.dumps({"gcs_uri": f"gs://test_bucket/test_runs/{session_id}/workspace.zip"}),
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
            await asyncio.gather(sender_task)

        return summary_payload

    async def test_multi_turn_agent_grpc_proxy_e2e(self):
        """Tests a complete multi-turn scenario with skills, MCP tools, and delegated scoring."""
        session_id = uuid.uuid4().hex
        summary = await self._run_client_session(
            session_id=session_id,
            run_config=self.run_config,
            skill_name="dataform_best_practices",
        )

        self.assertIsNotNone(summary)
        self.assertEqual(summary["total"], 1)
        self.assertEqual(summary["scores"]["trajectory_matcher"], 1)
        self.assertEqual(summary["scores"]["skills_trajectory"], 1)
        self.assertEqual(summary["scores"]["dataform_compile"], 1)

    async def test_concurrent_sessions_e2e(self):
        """Tests multiple concurrent evaluation sessions running simultaneously on the server."""
        num_sessions = 3
        session_ids = [uuid.uuid4().hex for _ in range(num_sessions)]

        async def run_one(sid):
            summary = await self._run_client_session(
                session_id=sid,
                run_config=self.run_config,
                skill_name="dataform_best_practices",
            )
            self.assertIsNotNone(summary)
            self.assertEqual(summary["total"], 1)
            self.assertEqual(summary["scores"]["trajectory_matcher"], 1)
            self.assertEqual(summary["scores"]["skills_trajectory"], 1)
            self.assertEqual(summary["scores"]["dataform_compile"], 1)

        await asyncio.gather(*(run_one(sid) for sid in session_ids))

    async def test_concurrent_multi_scenario_e2e(self):
        """Tests multiplexing multiple concurrent scenarios across threads within a single session."""
        session_id = uuid.uuid4().hex
        metadata = [("client-rpc-id", session_id)]

        await self.stub.Ping(eval_request_pb2.PingRequest(), metadata=metadata)
        await self.stub.Connect(eval_connect_pb2.EvalConnectRequest(bidirectional_stream=True), metadata=metadata)

        yaml_bytes = yaml.dump(self.multi_scenario_run_config).encode("utf-8")
        await self.stub.EvalConfig(eval_config_pb2.EvalConfigRequest(yaml_config=yaml_bytes), metadata=metadata)

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
                    prompt = msg.turn_request.prompt

                    if "dataform" in prompt:
                        sname, srv, tool = "dataform_best_practices", "dataform", "compile"
                    elif "dbt" in prompt:
                        sname, srv, tool = "dbt_best_practices", "dbt", "compile"
                    else:
                        sname, srv, tool = "sql_optimization", "bigquery", "execute_query"

                    t_skill = eval_agent_pb2.ToolCallRecord(
                        tool_id=f"call_{uuid.uuid4().hex[:6]}",
                        tool_name="activate_skill",
                        parameters_json=json.dumps({"skill_name": sname}),
                        output="Skill activated",
                        status="success",
                        duration_ms=20,
                    )
                    t_mcp = eval_agent_pb2.ToolCallRecord(
                        tool_id=f"call_{uuid.uuid4().hex[:6]}",
                        tool_name=f"{srv}__{tool}",
                        parameters_json=json.dumps({"action": tool}),
                        output=f"Executed {srv}__{tool}",
                        status="success",
                        duration_ms=100,
                    )
                    turn_resp = eval_agent_pb2.TurnResponse(
                        turn_index=1,
                        response_text=f"Completed {srv} action.",
                        tool_calls=[t_skill, t_mcp],
                        token_stats={"input_tokens": 120, "output_tokens": 60},
                        success=True,
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
                    metric = eval_agent_pb2.MetricScore(
                        metric_name=spec.scorer_name,
                        score=100.0,
                        comparison_logs=f"{spec.scorer_name} ok",
                        success=True,
                    )
                    reply = eval_agent_pb2.AgentStreamMessage(
                        session_id=session_id,
                        correlation_id=corr_id,
                        scoring_response=eval_agent_pb2.ScoringResponse(scores=[metric]),
                    )
                    await send_queue.put(reply)

                elif payload_type == "reporting_request":
                    rep_spec = msg.reporting_request.reporter
                    rep_res = eval_agent_pb2.ReporterResult(
                        reporter_name=rep_spec.reporter_name,
                        success=True,
                        result_json=json.dumps({"gcs_uri": f"gs://test_bucket/test_runs/{session_id}/workspace.zip"}),
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
            await asyncio.gather(sender_task)

        self.assertIsNotNone(summary_payload)
        self.assertEqual(summary_payload["total"], 3)
        self.assertEqual(summary_payload["scores"]["trajectory_matcher"], 3)
        self.assertEqual(summary_payload["scores"]["skills_trajectory"], 3)
        self.assertEqual(summary_payload["scores"]["dataform_compile"], 3)


if __name__ == "__main__":
    unittest.main()
