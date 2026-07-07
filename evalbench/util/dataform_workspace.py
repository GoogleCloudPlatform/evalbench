"""Utility for managing temporary GCP Dataform repositories and workspaces."""

import io
import logging
import os
import pathlib
import re
import zipfile

from google.api_core import exceptions as api_exceptions
from google.cloud import dataform_v1beta1

logger = logging.getLogger(__name__)

_WORKSPACE_RE = re.compile(
    r"^projects/[^/]+/locations/[^/]+/repositories/([^/]+)/workspaces/[^/]+$"
)


class DataformWorkspaceManager:
    """Helper class to interact with Google Cloud Dataform API."""

    def __init__(self, project_id: str, location: str):
        """Initializes the Dataform client helper.

        Args:
            project_id: The GCP Project ID.
            location: The GCP region (e.g. 'us-west4').
        """
        self.client = dataform_v1beta1.DataformClient()
        self.project_id = project_id
        self.location = location
        self.parent = f"projects/{project_id}/locations/{location}"

    def _create_repository(self, repository_id: str) -> str:
        """Creates a new Dataform repository in the project and location.

        Args:
            repository_id: The unique ID for the repository.

        Returns:
            The full resource path of the created repository.
        """
        repository_path = f"{self.parent}/repositories/{repository_id}"
        logger.info("Creating Dataform repository: %s", repository_path)

        # We create a clean, empty repository object.
        repository = dataform_v1beta1.Repository()

        try:
            response = self.client.create_repository(
                request={
                    "parent": self.parent,
                    "repository_id": repository_id,
                    "repository": repository,
                }
            )
            logger.info("Successfully created repository: %s", response.name)
            return response.name
        except Exception:
            logger.exception(
                "Failed to create repository: %s", repository_id
            )
            raise

    def _create_workspace(self, repository_id: str,
                          workspace_id: str) -> str:
        """Creates a new Dataform workspace inside the specified repository.

        Args:
            repository_id: The ID of the parent repository.
            workspace_id: The unique ID for the workspace.

        Returns:
            The full resource path of the created workspace.
        """
        repository_path = f"{self.parent}/repositories/{repository_id}"
        workspace_path = f"{repository_path}/workspaces/{workspace_id}"
        logger.info("Creating Dataform workspace: %s", workspace_path)

        workspace = dataform_v1beta1.Workspace()

        try:
            response = self.client.create_workspace(
                request={
                    "parent": repository_path,
                    "workspace_id": workspace_id,
                    "workspace": workspace,
                }
            )
            logger.info("Successfully created workspace: %s", response.name)
            return response.name
        except Exception:
            logger.exception(
                "Failed to create workspace %s in repo %s",
                workspace_id,
                repository_id,
            )
            raise

    def _delete_workspace(self, repository_id: str,
                          workspace_id: str) -> None:
        """Deletes a Dataform workspace inside the specified repository.

        Args:
            repository_id: The ID of the parent repository.
            workspace_id: The unique ID for the workspace.
        """
        repository_path = f"{self.parent}/repositories/{repository_id}"
        workspace_path = f"{repository_path}/workspaces/{workspace_id}"
        logger.info("Deleting Dataform workspace: %s", workspace_path)

        try:
            self.client.delete_workspace(request={"name": workspace_path})
            logger.info("Successfully deleted workspace: %s", workspace_path)
        except api_exceptions.NotFound:
            logger.warning("Workspace already deleted: %s", workspace_path)
        except Exception:
            logger.exception(
                "Failed to delete workspace %s in repo %s",
                workspace_id,
                repository_id,
            )
            raise

    def _delete_repository(self, repository_id: str) -> None:
        """Deletes a Dataform repository and all its nested resources.

        This performs a cascading delete by first programmatically deleting
        all workspaces inside the repository, and then deleting the
        repository itself with the force flag enabled.

        Args:
            repository_id: The ID of the repository to delete.
        """
        repository_path = f"{self.parent}/repositories/{repository_id}"
        logger.info("Deleting Dataform repository: %s", repository_path)

        try:
            workspaces = self.client.list_workspaces(
                request={"parent": repository_path}
            )
            for ws in workspaces:
                ws_id = ws.name.split("/")[-1]
                self._delete_workspace(repository_id, ws_id)

            self.client.delete_repository(
                request={"name": repository_path, "force": True}
            )
            logger.info(
                "Successfully deleted repository and nested resources: %s",
                repository_path,
            )
        except Exception:
            logger.exception(
                "Failed to delete repository and nested resources: %s",
                repository_id,
            )
            raise

    def setup_workspace(
        self,
        job_id: str,
        scenario_id: str = "default",
        env_files_dir: str | None = None,
    ) -> str:
        """Dynamically creates a Dataform repository and workspace."""
        repository_id = f"evalbench-{job_id}"
        repository_path = self._create_repository(repository_id)

        workspace_id = scenario_id
        workspace_path = self._create_workspace(repository_id, workspace_id)

        if env_files_dir:
            base_path = pathlib.Path(env_files_dir)
            if not base_path.is_dir():
                raise ValueError(
                    f"env_files_dir is not a valid directory: {env_files_dir}"
                )

            logger.info(
                "Uploading setup files from directory: %s", env_files_dir
            )
            for root, _, files in os.walk(env_files_dir):
                for file in files:
                    local_file_path = pathlib.Path(root) / file
                    relative_path = local_file_path.relative_to(base_path)

                    with open(local_file_path, "rb") as f:
                        raw_bytes = f.read()

                    self.client.write_file(
                        request={
                            "workspace": workspace_path,
                            "path": relative_path.as_posix(),
                            "contents": raw_bytes,
                        }
                    )
                    logger.info("Injected setup file: %s", relative_path)

        return workspace_path

    def download_and_zip(self, workspace_uri: str) -> bytes:
        """Downloads all workspace files and compresses them into ZIP bytes."""
        logger.info("Downloading and zipping workspace: %s...", workspace_uri)
        try:
            page_result = self.client.search_files(
                request={"workspace": workspace_uri}
            )
            file_count = 0
            with io.BytesIO() as zip_buffer:
                with zipfile.ZipFile(
                    zip_buffer, "w", zipfile.ZIP_DEFLATED
                ) as zipf:
                    for result in page_result:
                        if result.file and result.file.path:
                            file_path = result.file.path
                            file_response = self.client.read_file(
                                request={
                                    "workspace": workspace_uri,
                                    "path": file_path,
                                }
                            )
                            zipf.writestr(
                                file_path, file_response.file_contents
                            )
                            logger.info(
                                "Added file to archive: %s", file_path
                            )
                            file_count += 1

                zip_bytes = zip_buffer.getvalue()

            logger.info(
                "Successfully compressed %d files (%d bytes)",
                file_count,
                len(zip_bytes),
            )
            return zip_bytes
        except Exception:
            logger.exception(
                "Failed to download and zip workspace %s", workspace_uri
            )
            raise

    def teardown_workspace(self, workspace_uri: str) -> None:
        """Deletes the parent Dataform repository and all child workspaces."""
        workspace_uri_match = _WORKSPACE_RE.match(workspace_uri)
        if not workspace_uri_match:
            raise ValueError(f"Invalid workspace URI: {workspace_uri!r}")
        repository_id = workspace_uri_match.group(1)
        self._delete_repository(repository_id)
