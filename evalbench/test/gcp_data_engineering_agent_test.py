import asyncio
import concurrent.futures.thread
import datetime
import json
import logging
import os
import sys
import threading
import time
from unittest.mock import MagicMock, patch

import pytest
from google.auth.exceptions import DefaultCredentialsError, RefreshError

from a2a.client.base_client import BaseClient
from a2a.client.transports.base import ClientTransport
from a2a.types import a2a_pb2 as pb
from a2a.utils import TransportProtocol

# Add generators path to system path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataset.dataengineeringagentinput import EvalDeaRequest  # noqa: E402
from evaluator.dataengineeringagentorchestrator import (  # noqa: E402
    DataEngineeringAgentOrchestrator,
)
from evaluator.dataengineeringagentevaluator import (  # noqa: E402
    DataEngineeringAgentEvaluator,
)
from generators.models import get_generator  # noqa: E402
from generators.models.gcp_data_engineering_agent import (  # noqa: E402
    DataEngineeringAgentGenerator,
    GcpAdcCredentialService,
    CONVERSATION_TOKEN_URI,
    MESSAGE_LEVEL_URI,
    _find_agent_text_recursive,
)


@pytest.mark.anyio
async def test_get_credentials_invalid_scheme():
    service = GcpAdcCredentialService()

    with pytest.raises(ValueError) as excinfo:
        await service.get_credentials("basic", None)

    assert "only services 'oauth' or 'oauth2'" in str(excinfo.value)


def test_generator_setup_missing_project_id():
    config = {
        "generator": "data_engineering_agent",
        "gcp_region": "us-west4",
        "target_workspace": "projects/test-workspace",
    }
    with pytest.raises(ValueError) as excinfo:
        DataEngineeringAgentGenerator(config)
    assert "gcp_project_id' is required" in str(excinfo.value)


def test_generator_setup_missing_workspace():
    config = {
        "generator": "data_engineering_agent",
        "gcp_project_id": "test",
        "gcp_region": "us-west4",
    }
    with pytest.raises(ValueError) as excinfo:
        DataEngineeringAgentGenerator(config)
    assert "target_workspace' is required" in str(excinfo.value)


@pytest.mark.anyio
@patch("google.auth.default")
async def test_get_credentials_error_resiliency_default(mock_auth_default):
    mock_auth_default.side_effect = DefaultCredentialsError(
        "Credentials missing."
    )
    service = GcpAdcCredentialService()

    with pytest.raises(DefaultCredentialsError):
        await service.get_credentials("oauth", None)


@pytest.mark.anyio
@patch("google.auth.default")
async def test_get_credentials_error_resiliency_refresh(mock_auth_default):
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.refresh.side_effect = RefreshError("Network timed out.")
    mock_auth_default.return_value = (mock_creds, "test-project")

    service = GcpAdcCredentialService()

    with pytest.raises(RefreshError):
        await service.get_credentials("oauth", None)


def test_generator_setup_invalid_workspace_characters():
    config = {
        "generator": "data_engineering_agent",
        "gcp_project_id": "test-project-123",
        "gcp_region": "us-east1",
        "target_workspace": (
            "projects/test-project/locations/us-east1/repositories/test-repo/"
            "workspaces/test-workspace; rm -rf /"
        ),
    }
    with patch("google.auth.default") as mock_auth_default:
        mock_creds = MagicMock()
        mock_auth_default.return_value = (mock_creds, "test-project")

        with pytest.raises(ValueError) as excinfo:
            DataEngineeringAgentGenerator(config)
        assert "target_workspace' contains invalid characters" in str(
            excinfo.value
        )


@pytest.mark.anyio
async def test_gcp_adc_credential_service_concurrency():
    service = GcpAdcCredentialService()
    mock_creds = MagicMock()
    mock_creds.valid = False
    mock_creds.token = "stub-token"

    def slow_refresh(*args, **kwargs):
        time.sleep(0.2)
        mock_creds.valid = True

    mock_creds.refresh.side_effect = slow_refresh

    with patch("google.auth.default") as mock_auth_default:
        mock_auth_default.return_value = (mock_creds, "test-project")

        results = []
        errors = []

        def run_in_thread():
            try:
                token = asyncio.run(service.get_credentials("oauth", None))
                results.append(token)
            except Exception as e:
                errors.append(e)

        num_threads = 3
        threads = [
            threading.Thread(target=run_in_thread, daemon=True)
            for _ in range(num_threads)
        ]
        for t in threads:
            t.start()

        for t in threads:
            t.join(timeout=3.0)
            if t.is_alive():
                pytest.fail("Deadlock detected in credential service")

        if errors:
            pytest.fail(f"Threads raised errors: {errors}")

        assert len(results) == num_threads
        assert all(t == "stub-token" for t in results)
        assert mock_creds.refresh.call_count == 1


def test_data_engineering_agent_generator_setup():
    config = {
        "generator": "data_engineering_agent",
        "gcp_project_id": "test-project-123",
        "gcp_region": "us-east1",
        "target_workspace": (
            "projects/diff-project-abc/locations/diff-region-xyz/repositories/"
            "test-repo/workspaces/test-workspace"
        ),
    }

    # Mock google.auth.default during initialization
    with patch("google.auth.default") as mock_auth_default:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_auth_default.return_value = (mock_creds, "test-project")

        generator = DataEngineeringAgentGenerator(config)

        assert generator.name == "data_engineering_agent"
        expected_endpoint = (
            "https://geminidataanalytics.googleapis.com/v1/a2a/projects/"
            "test-project-123/locations/us-east1/agents/dataengineeringagent"
        )
        assert generator.endpoint == expected_endpoint
        assert generator.target_workspace == config["target_workspace"]
        assert generator.auth_interceptor is not None


@pytest.mark.anyio
@patch("google.auth.default")
@patch("generators.models.gcp_data_engineering_agent.create_client")
async def test_generate_internal_conversation_token_caching(
    mock_create_client, mock_auth_default
):
    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_auth_default.return_value = (mock_creds, "test-project")

    mock_client = MagicMock()
    sent_requests = []

    async def mock_send_message(request, *args, **kwargs):
        sent_requests.append(request)
        resp = pb.SendMessageResponse()
        resp.task.metadata[CONVERSATION_TOKEN_URI] = "stub-token-xyz"
        msg = pb.Message(role=pb.ROLE_AGENT)
        msg.metadata[MESSAGE_LEVEL_URI] = "USER"
        msg.parts.append(pb.Part(text="Response text"))
        resp.task.history.append(msg)
        yield resp

    mock_client.send_message.side_effect = mock_send_message
    mock_client.close.side_effect = lambda *a, **k: asyncio.sleep(0)
    mock_create_client.return_value = mock_client

    config = {
        "generator": "data_engineering_agent",
        "gcp_project_id": "test",
        "gcp_region": "us-west4",
        "target_workspace": "test-workspace",
    }
    generator = DataEngineeringAgentGenerator(config)

    # First call: no token cached yet
    req1 = EvalDeaRequest({
        "starting_prompt": "First message",
        "id": "conv-id-1",
    })
    generator.generate_internal(req1)

    assert CONVERSATION_TOKEN_URI not in sent_requests[0].metadata
    assert generator._conversation_token_cache["conv-id-1"] == "stub-token-xyz"

    # Second call (same conv ID): should send cached token
    req2 = EvalDeaRequest({
        "starting_prompt": "Second message",
        "id": "conv-id-1",
    })
    generator.generate_internal(req2)

    assert (
        sent_requests[1].metadata[CONVERSATION_TOKEN_URI]
        == "stub-token-xyz"
    )

    # Third call (different conv ID): should NOT send cached token
    req3 = EvalDeaRequest({
        "starting_prompt": "First message for conv 2",
        "id": "conv-id-2",
    })
    generator.generate_internal(req3)

    assert CONVERSATION_TOKEN_URI not in sent_requests[2].metadata


@pytest.mark.anyio
@patch("google.auth.default")
@patch("generators.models.gcp_data_engineering_agent.create_client")
async def test_generate_internal_uses_minimal_agent_card(
    mock_create_client, mock_auth_default
):
    mock_creds = MagicMock()
    mock_creds.valid = True
    mock_auth_default.return_value = (mock_creds, "test-project")
    mock_client = MagicMock()

    async def mock_send_message(*args, **kwargs):
        resp = pb.SendMessageResponse()
        resp.task.metadata[
            "https://geminidataanalytics.googleapis.com/a2a/extensions/"
            "conversationtoken/v1"
        ] = "stub-token-123"
        msg = pb.Message(role=pb.ROLE_AGENT)
        msg.metadata[
            "https://geminidataanalytics.googleapis.com/a2a/extensions/"
            "messagelevel/v1"
        ] = "USER"
        msg.parts.append(pb.Part(text="Analysis complete."))
        resp.task.history.append(msg)
        yield resp

    mock_client.send_message.side_effect = mock_send_message

    async def mock_close():
        pass
    mock_client.close.side_effect = mock_close
    mock_create_client.return_value = mock_client

    config = {
        "generator": "data_engineering_agent",
        "gcp_project_id": "test",
        "gcp_region": "us-west4",
        "target_workspace": (
            "projects/test/locations/us-west4/repositories/"
            "test-repo/workspaces/test-workspace"
        ),
    }

    generator = DataEngineeringAgentGenerator(config)
    req = EvalDeaRequest({
        "starting_prompt": "Please analyze table schema.",
        "id": "test-conv-id",
    })
    result = generator.generate_internal(req)

    assert result.generated_nl_response == "Analysis complete."
    mock_create_client.assert_called_once()
    called_card = mock_create_client.call_args[0][0]
    assert len(called_card.supported_interfaces) == 1
    expected_endpoint = (
        "https://geminidataanalytics.googleapis.com/v1/a2a/projects/"
        "test/locations/us-west4/agents/dataengineeringagent"
    )
    assert called_card.supported_interfaces[0].url == expected_endpoint
    assert (
        called_card.supported_interfaces[0].protocol_binding
        == TransportProtocol.HTTP_JSON
    )
    assert called_card.capabilities.extended_agent_card is True


@patch("evaluator.dataengineeringagentevaluator.AgentScoreWork")
@patch("evaluator.dataengineeringagentevaluator.SimulatedUser")
def test_evaluator_process_scenario(
    mock_sim_user_class, mock_score_work_class
):
    mock_sim_user = MagicMock()
    mock_sim_user.get_next_response.side_effect = [
        "What is target workspace?",
        "TERMINATE",
    ]
    mock_sim_user_class.return_value = mock_sim_user

    mock_score_work = MagicMock()
    mock_score_work_class.return_value = mock_score_work

    config = {
        "generator": "data_engineering_agent",
        "gcp_project_id": "test-project",
        "gcp_region": "us-east1",
        "target_workspace": "test-workspace",
    }
    evaluator = DataEngineeringAgentEvaluator(config)

    mock_generator = MagicMock()

    def mock_generate(eval_result):
        if eval_result.nl_prompt == "hello":
            eval_result.generated_nl_response = "Hi user!"
        elif eval_result.nl_prompt == "What is target workspace?":
            eval_result.generated_nl_response = "It is test-workspace."
    mock_generator.generate.side_effect = mock_generate
    evaluator.generator = mock_generator

    scenario = {
        "id": "scenario-1",
        "starting_prompt": "hello",
        "max_turns": 3,
        "conversation_plan": ["Ask query"],
    }
    eval_result = EvalDeaRequest({
        "starting_prompt": "hello",
        "id": "scenario-1",
    })

    result = evaluator.process_scenario(
        scenario,
        eval_result,
        "job-1",
        {"metadata": "test"},
        simulated_user=mock_sim_user,
    )

    assert len(result.agent_results) == 1
    eval_output = result.agent_results[0]
    history = json.loads(eval_output["conversation_history"])
    assert history == [
        {"user": "hello", "agent": "Hi user!"},
        {
            "user": "What is target workspace?",
            "agent": "It is test-workspace.",
        },
    ]
    assert eval_output["stdout"] == "It is test-workspace."

    mock_score_work_class.assert_called_once()
    mock_score_work.run.assert_called_once()


@patch(
    "evaluator.dataengineeringagentorchestrator.DataEngineeringAgentEvaluator"
)
def test_orchestrator_evaluate(mock_evaluator_class):
    mock_evaluator = MagicMock()
    mock_evaluator.evaluate.return_value = (
        ["mock-eval-output"],
        ["mock-scoring-result"],
    )
    mock_evaluator_class.return_value = mock_evaluator

    config = {"generator": "data_engineering_agent"}
    orchestrator = DataEngineeringAgentOrchestrator(config)
    dataset = [EvalDeaRequest({"starting_prompt": "test", "id": "1"})]

    orchestrator.evaluate(dataset)

    mock_evaluator_class.assert_called_once_with(config)
    mock_evaluator.evaluate.assert_called_once_with(
        dataset, orchestrator.job_id, orchestrator.run_time
    )
    assert orchestrator.total_eval_outputs == ["mock-eval-output"]
    assert orchestrator.total_scoring_results == ["mock-scoring-result"]


def test_orchestrator_process():
    config = {"generator": "data_engineering_agent"}
    orchestrator = DataEngineeringAgentOrchestrator(config)
    orchestrator.total_eval_outputs = [{"result": "success"}]
    orchestrator.total_scoring_results = [{"score": 1}]

    job_id, run_time, results_tf, scores_tf, err = orchestrator.process()

    assert job_id == orchestrator.job_id
    assert run_time == orchestrator.run_time
    assert err is None

    with open(results_tf, "r") as f:
        assert json.load(f) == [{"result": "success"}]
    with open(scores_tf, "r") as f:
        assert json.load(f) == [{"score": 1}]

    os.unlink(results_tf)
    os.unlink(scores_tf)


@patch("generators.models.gcp_data_engineering_agent.logger")
def test_log_api_error_details_httpx_json(mock_logger):
    # Mock httpx-style exception with JSON error message
    mock_response = MagicMock()
    mock_response.status_code = 403
    mock_response.text = '{"error": {"message": "GCP Permission Denied"}}'

    mock_exception = Exception("HTTP Error")
    mock_exception.response = mock_response

    DataEngineeringAgentGenerator._log_api_error_details(mock_exception)

    mock_logger.exception.assert_called_once_with(
        "API Error (Status 403): GCP Permission Denied"
    )


def test_extract_reply_text_heuristic_standard_node():
    resp = pb.SendMessageResponse()
    resp.message.role = pb.ROLE_AGENT
    resp.message.parts.append(pb.Part(text="Direct message text"))

    assert (
        DataEngineeringAgentGenerator._extract_reply_text(resp)
        == "Direct message text"
    )


def test_extract_reply_text_heuristic_recursive_fallback():
    nested_msg = pb.Message(role=pb.ROLE_AGENT)
    nested_msg.parts.append(pb.Part(text="Nested message text"))

    container = [{"some_key": "some_value"}, [nested_msg]]
    assert _find_agent_text_recursive(container) == "Nested message text"
