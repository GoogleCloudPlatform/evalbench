"""Unit tests for DataformHelper utility."""

from typing import Generator
from unittest.mock import MagicMock, patch

from google.api_core import exceptions as api_exceptions
from google.cloud import dataform_v1beta1
import pytest
from util.dataform import DataformHelper

PROJECT_ID = "test-project"
LOCATION = "us-west4"
REPO_ID = "test-repo"
WORKSPACE_ID = "test-workspace"


@pytest.fixture(name="mock_client")
def fixture_mock_client() -> Generator[MagicMock, None, None]:
    with patch("util.dataform.dataform_v1beta1.DataformClient") as mock_class:
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance
        yield mock_instance


@pytest.fixture(name="helper")
def fixture_helper(mock_client: MagicMock) -> DataformHelper:
    del mock_client
    return DataformHelper(PROJECT_ID, LOCATION)


def test_create_repository_success(
    mock_client: MagicMock, helper: DataformHelper
):
    mock_response = MagicMock()
    mock_response.name = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/repositories/{REPO_ID}"
    )
    mock_client.create_repository.return_value = mock_response

    repo_name = helper.create_repository(REPO_ID)

    assert repo_name == mock_response.name
    mock_client.create_repository.assert_called_once()


def test_create_repository_generic_exception(
    mock_client: MagicMock, helper: DataformHelper
):
    mock_client.create_repository.side_effect = Exception("failed")

    with pytest.raises(Exception) as exc_info:
        helper.create_repository(REPO_ID)

    assert "failed" in str(exc_info.value)
    mock_client.create_repository.assert_called_once()


def test_create_workspace_success(
    mock_client: MagicMock, helper: DataformHelper
):
    mock_response = MagicMock()
    mock_response.name = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/repositories/{REPO_ID}/workspaces/{WORKSPACE_ID}"
    )
    mock_client.create_workspace.return_value = mock_response

    workspace_name = helper.create_workspace(REPO_ID, WORKSPACE_ID)

    assert workspace_name == mock_response.name
    mock_client.create_workspace.assert_called_once()


def test_create_workspace_generic_exception(
    mock_client: MagicMock, helper: DataformHelper
):
    mock_client.create_workspace.side_effect = Exception("failed")

    with pytest.raises(Exception) as exc_info:
        helper.create_workspace(REPO_ID, WORKSPACE_ID)

    assert "failed" in str(exc_info.value)
    mock_client.create_workspace.assert_called_once()


def test_delete_workspace_success(
    mock_client: MagicMock, helper: DataformHelper
):
    helper.delete_workspace(REPO_ID, WORKSPACE_ID)

    mock_client.delete_workspace.assert_called_once_with(
        request={
            "name": (
                f"projects/{PROJECT_ID}/locations/{LOCATION}"
                f"/repositories/{REPO_ID}/workspaces/{WORKSPACE_ID}"
            )
        }
    )


def test_delete_workspace_not_found(
    mock_client: MagicMock, helper: DataformHelper
):
    mock_client.delete_workspace.side_effect = (
        api_exceptions.NotFound("not found")
    )

    helper.delete_workspace(REPO_ID, WORKSPACE_ID)

    mock_client.delete_workspace.assert_called_once()


def test_delete_workspace_exception(
    mock_client: MagicMock, helper: DataformHelper
):
    mock_client.delete_workspace.side_effect = Exception("failed")

    with pytest.raises(Exception) as exc_info:
        helper.delete_workspace(REPO_ID, WORKSPACE_ID)

    assert "failed" in str(exc_info.value)
    mock_client.delete_workspace.assert_called_once()


def test_delete_repository_success(
    mock_client: MagicMock, helper: DataformHelper
):
    # Mock list_workspaces to return two workspaces
    ws1 = MagicMock()
    ws1.name = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/repositories/{REPO_ID}/workspaces/ws1"
    )
    ws2 = MagicMock()
    ws2.name = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/repositories/{REPO_ID}/workspaces/ws2"
    )
    mock_client.list_workspaces.return_value = [ws1, ws2]

    # Patch the helper's own delete_workspace method to verify delegation
    with patch.object(helper, "delete_workspace") as mock_delete_ws:
        helper.delete_repository(REPO_ID)

        mock_client.list_workspaces.assert_called_once()
        assert mock_delete_ws.call_count == 2
        mock_delete_ws.assert_any_call(REPO_ID, "ws1")
        mock_delete_ws.assert_any_call(REPO_ID, "ws2")

    mock_client.delete_repository.assert_called_once_with(
        request={
            "name": (
                f"projects/{PROJECT_ID}/locations/{LOCATION}"
                f"/repositories/{REPO_ID}"
            ),
            "force": True,
        }
    )


def test_delete_repository_exception(
    mock_client: MagicMock, helper: DataformHelper
):
    mock_client.list_workspaces.side_effect = Exception("failed")

    with pytest.raises(Exception) as exc_info:
        helper.delete_repository(REPO_ID)

    assert "failed" in str(exc_info.value)
