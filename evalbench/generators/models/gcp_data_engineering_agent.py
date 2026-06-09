import asyncio
import logging
import threading
from typing import Any

from a2a.client import ClientCallContext
from a2a.client.auth import AuthInterceptor, CredentialService
import google.auth
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.auth.transport.requests import Request

from .generator import QueryGenerator


logger = logging.getLogger(__name__)


class GcpAdcCredentialService(CredentialService):
    """GCP Application Default Credentials (ADC) service for A2A SDK.

    This provider only services OAuth/OAuth2 schemes.
    Thread-safe and Loop-safe implementation utilizing standard threading.Lock
    and a fast-path check to avoid thread pool overhead for valid tokens.
    """

    def __init__(self):
        self.credentials = None
        self._lock = threading.Lock()

    async def get_credentials(
        self,
        security_scheme_name: str,
        context: ClientCallContext | None,
    ) -> str:
        if security_scheme_name.lower() not in ("oauth", "oauth2"):
            raise ValueError(
                f"GcpAdcCredentialService only services 'oauth' or 'oauth2' "
                f"schemes, got '{security_scheme_name}'"
            )

        try:
            return await asyncio.to_thread(self._get_and_refresh_token)
        except (DefaultCredentialsError, RefreshError) as e:
            logger.error("GCP ADC authentication failed: %s", e)
            raise
        except Exception as e:
            logger.exception(
                "Unexpected error while retrieving GCP ADC credentials: %s", e
            )
            raise

    def _get_and_refresh_token(self) -> str:
        with self._lock:
            if self.credentials is None:
                logger.debug(
                    "Initializing GCP Application Default Credentials."
                )
                credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                self.credentials = credentials

            if not self.credentials.valid:
                logger.debug(
                    "GCP ADC token is invalid or expired. Refreshing..."
                )
                self.credentials.refresh(Request())

            token_val = self.credentials.token
            if not token_val:
                raise ValueError(
                    "GCP ADC token is empty after retrieval/refresh."
                )

            return token_val


class DataEngineeringAgentGenerator(QueryGenerator):
    """Data Engineering Agent (DEA) Query Generator using the A2A SDK."""

    def __init__(self, querygenerator_config: dict[str, Any]):
        super().__init__(querygenerator_config)
        self.name = "data_engineering_agent"
        gcp_project_id = querygenerator_config.get("gcp_project_id", "")
        gcp_region = querygenerator_config.get("gcp_region", "")

        if not gcp_project_id:
            raise ValueError(
                "Configuration key 'gcp_project_id' is required for "
                "DataEngineeringAgentGenerator."
            )
        if not gcp_region:
            raise ValueError(
                "Configuration key 'gcp_region' is required for "
                "DataEngineeringAgentGenerator."
            )

        self.endpoint = (
            f"https://geminidataanalytics.googleapis.com/v1/a2a/projects/"
            f"{gcp_project_id}/locations/{gcp_region}/"
            f"agents/dataengineeringagent"
        )
        self.target_workspace = querygenerator_config.get(
            "target_workspace", ""
        )

        if not self.target_workspace:
            raise ValueError(
                "Configuration key 'target_workspace' is required for "
                "DataEngineeringAgentGenerator."
            )

        workspace_chars = (
            self.target_workspace.replace("/", "")
            .replace("-", "")
            .replace("_", "")
        )
        if not workspace_chars.isalnum():
            raise ValueError(
                "Configuration key 'target_workspace' contains invalid "
                f"characters: '{self.target_workspace}'"
            )

        self.auth_interceptor = AuthInterceptor(GcpAdcCredentialService())
        logger.info(
            "A2A AuthInterceptor successfully configured with "
            "GcpAdcCredentialService."
        )

    def generate_internal(self, prompt: str) -> Any:
        """Stubbed messaging logic for WIP scaffolding (Task 1.3)."""
        raise NotImplementedError(
            "Task 1.3 DEA A2A messaging logic in generate_internal is "
            "not yet implemented."
        )
