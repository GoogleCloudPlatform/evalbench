"""Unit tests for DataformWorkspaceManager utility."""

import io
import pathlib
import tempfile
from typing import Generator
from unittest.mock import MagicMock, patch
import zipfile

from google.api_core import exceptions as api_exceptions
from google.cloud import dataform_v1beta1
import pytest
from util.dataform_workspace import DataformWorkspaceManager

PROJECT_ID = "test-project"
LOCATION = "us-west4"
REPO_ID = "test-repo"
WORKSPACE_ID = "test-workspace"
WORKSPACE_URI = (
    f"projects/{PROJECT_ID}/locations/{LOCATION}/repositories/"
    "evalbench-job-123/workspaces/default"
)


@pytest.fixture(name="mock_client")
def fixture_mock_client() -> Generator[MagicMock, None, None]:
    with patch(
        "util.dataform_workspace.dataform_v1beta1.DataformClient"
    ) as mock_class:
        mock_instance = MagicMock()
        mock_class.return_value = mock_instance
        yield mock_instance


@pytest.fixture(name="helper")
def fixture_helper(mock_client: MagicMock) -> DataformWorkspaceManager:
    del mock_client
    return DataformWorkspaceManager(PROJECT_ID, LOCATION)


def test_create_repository_success(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_response = MagicMock()
    mock_response.name = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/repositories/{REPO_ID}"
    )
    mock_client.create_repository.return_value = mock_response

    repo_name = helper._create_repository(REPO_ID)

    assert repo_name == mock_response.name
    mock_client.create_repository.assert_called_once()


def test_create_repository_generic_exception(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_client.create_repository.side_effect = Exception("failed")

    with pytest.raises(Exception) as exc_info:
        helper._create_repository(REPO_ID)

    assert "failed" in str(exc_info.value)
    mock_client.create_repository.assert_called_once()


def test_create_workspace_success(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_response = MagicMock()
    mock_response.name = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}"
        f"/repositories/{REPO_ID}/workspaces/{WORKSPACE_ID}"
    )
    mock_client.create_workspace.return_value = mock_response

    workspace_name = helper._create_workspace(REPO_ID, WORKSPACE_ID)

    assert workspace_name == mock_response.name
    mock_client.create_workspace.assert_called_once()


def test_create_workspace_generic_exception(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_client.create_workspace.side_effect = Exception("failed")

    with pytest.raises(Exception) as exc_info:
        helper._create_workspace(REPO_ID, WORKSPACE_ID)

    assert "failed" in str(exc_info.value)
    mock_client.create_workspace.assert_called_once()


def test_delete_workspace_success(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    helper._delete_workspace(REPO_ID, WORKSPACE_ID)

    mock_client.delete_workspace.assert_called_once_with(
        request={
            "name": (
                f"projects/{PROJECT_ID}/locations/{LOCATION}"
                f"/repositories/{REPO_ID}/workspaces/{WORKSPACE_ID}"
            )
        }
    )


def test_delete_workspace_not_found(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_client.delete_workspace.side_effect = (
        api_exceptions.NotFound("not found")
    )

    helper._delete_workspace(REPO_ID, WORKSPACE_ID)

    mock_client.delete_workspace.assert_called_once()


def test_delete_workspace_exception(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_client.delete_workspace.side_effect = Exception("failed")

    with pytest.raises(Exception) as exc_info:
        helper._delete_workspace(REPO_ID, WORKSPACE_ID)

    assert "failed" in str(exc_info.value)
    mock_client.delete_workspace.assert_called_once()


def test_delete_repository_success(
    mock_client: MagicMock, helper: DataformWorkspaceManager
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

    # Patch the helper's own _delete_workspace method to verify delegation
    with patch.object(helper, "_delete_workspace") as mock_delete_ws:
        helper._delete_repository(REPO_ID)

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
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_client.list_workspaces.side_effect = Exception("failed")

    with pytest.raises(Exception) as exc_info:
        helper._delete_repository(REPO_ID)

    assert "failed" in str(exc_info.value)


def test_setup_workspace_success(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_repo_resp = MagicMock()
    mock_repo_resp.name = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}/repositories/"
        "evalbench-job-123"
    )
    mock_client.create_repository.return_value = mock_repo_resp

    mock_ws_resp = MagicMock()
    mock_ws_resp.name = WORKSPACE_URI
    mock_client.create_workspace.return_value = mock_ws_resp

    uri = helper.setup_workspace("job-123", "default")
    assert uri == WORKSPACE_URI
    assert mock_client.create_repository.called
    assert mock_client.create_workspace.called


def test_download_and_zip_success(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    result_file = MagicMock()
    result_file.file.path = "definitions/my_view.sqlx"
    mock_client.search_files.return_value = [result_file]

    mock_file_response = MagicMock()
    mock_file_response.file_contents = b"SELECT 1 as value"
    mock_client.read_file.return_value = mock_file_response

    zip_bytes = helper.download_and_zip(WORKSPACE_URI)

    with zipfile.ZipFile(io.BytesIO(zip_bytes)) as zip_file:
        assert zip_file.namelist() == ["definitions/my_view.sqlx"]
        assert (
            zip_file.read("definitions/my_view.sqlx")
            == b"SELECT 1 as value"
        )


def test_teardown_workspace_success(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_ws = MagicMock()
    mock_ws.name = f"{WORKSPACE_URI}"
    mock_client.list_workspaces.return_value = [mock_ws]

    helper.teardown_workspace(WORKSPACE_URI)
    expected_repo = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}/repositories/"
        "evalbench-job-123"
    )
    mock_client.delete_workspace.assert_called_once_with(
        request={"name": mock_ws.name}
    )
    mock_client.delete_repository.assert_called_once_with(
        request={"name": expected_repo, "force": True}
    )


def test_teardown_workspace_invalid_uri(helper: DataformWorkspaceManager):
    invalid_uri = (
        "projects/my-project/locations/us-central1/"
        "invalid/repo-id/workspaces/default"
    )
    with pytest.raises(ValueError) as exc_info:
        helper.teardown_workspace(invalid_uri)
    assert "Invalid workspace URI" in str(exc_info.value)


def test_setup_workspace_with_env_files_dir_success(
    mock_client: MagicMock, helper: DataformWorkspaceManager
):
    mock_repo_resp = MagicMock()
    mock_repo_resp.name = (
        f"projects/{PROJECT_ID}/locations/{LOCATION}/repositories/"
        "evalbench-job-123"
    )
    mock_client.create_repository.return_value = mock_repo_resp

    mock_ws_resp = MagicMock()
    mock_ws_resp.name = WORKSPACE_URI
    mock_client.create_workspace.return_value = mock_ws_resp

    with tempfile.TemporaryDirectory() as temp_dir:
        temp_dir_path = pathlib.Path(temp_dir)

        # Create a file in root
        conf_file = temp_dir_path / "workflow_settings.yaml"
        conf_file.write_text("project: $PROJECT_ID\nregion: $REGION")

        # Create a file in subdirectory
        subdir = temp_dir_path / "definitions"
        subdir.mkdir()
        sqlx_file = subdir / "my_view.sqlx"
        sqlx_file.write_text("SELECT 1 AS val")

        uri = helper.setup_workspace(
            "job-123", "default", env_files_dir=temp_dir
        )

        assert uri == WORKSPACE_URI
        assert mock_client.create_repository.called
        assert mock_client.create_workspace.called

        # Verify write_file calls
        assert mock_client.write_file.call_count == 2

        # Get actual calls
        calls = mock_client.write_file.call_args_list

        # Check call contents
        paths_called = [call.kwargs["request"]["path"] for call in calls]
        assert "workflow_settings.yaml" in paths_called
        assert "definitions/my_view.sqlx" in paths_called

        for call in calls:
            req = call.kwargs["request"]
            assert req["workspace"] == WORKSPACE_URI
            if req["path"] == "workflow_settings.yaml":
                assert req["contents"] == (
                    b"project: $PROJECT_ID\nregion: $REGION"
                )
            elif req["path"] == "definitions/my_view.sqlx":
                assert req["contents"] == b"SELECT 1 AS val"
