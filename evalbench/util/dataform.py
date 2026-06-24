"""Utility for managing temporary GCP Dataform repositories and workspaces."""

import logging

from google.api_core import exceptions as api_exceptions
from google.cloud import dataform_v1beta1

logger = logging.getLogger(__name__)


class DataformHelper:
    """Helper class to interact with Google Cloud Dataform API."""

    def __init__(self, project_id: str, location: str):
        """Initializes the Dataform client helper.

        Args:
            project_id: The GCP Project ID.
            location: The GCP region (e.g. 'us-west4').
        """
        self.client = dataform_v1beta1.DataformClient()
        self.parent = f"projects/{project_id}/locations/{location}"

    def create_repository(self, repository_id: str) -> str:
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

    def create_workspace(self, repository_id: str,
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

    def delete_workspace(self, repository_id: str,
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

    def delete_repository(self, repository_id: str) -> None:
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
                self.delete_workspace(repository_id, ws_id)

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
