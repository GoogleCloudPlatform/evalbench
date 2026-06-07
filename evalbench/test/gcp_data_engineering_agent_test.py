import os
import sys
from unittest.mock import MagicMock, patch
import pytest
from google.auth.exceptions import DefaultCredentialsError, RefreshError

# Add generators path to system path
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models import get_generator  # noqa: E402
from generators.models.gcp_data_engineering_agent import (  # noqa: E402
    DataEngineeringAgentGenerator,
    GcpAdcCredentialService,
)


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
