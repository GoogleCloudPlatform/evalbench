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

    Thread-safe and Loop-safe implementation utilizing standard threading.Lock
    and a fast-path check to avoid thread pool overhead for valid tokens.
    """

    def __init__(self) -> None:
        super().__init__()
        self.logger = logging.getLogger(__name__)
        self.credentials = None
        # Thread-safe lock for cross-thread/cross-loop synchronization
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

        # 1. Fast Path (Lock-free, Thread-free):
        # If credentials already exist and are valid, return the cached token immediately.
        # This avoids the CPU/latency overhead of asyncio.to_thread and lock acquisition.
        creds = self.credentials
        if creds is not None and creds.valid:
            token = creds.token
            if token is not None:
                return token

        # 2. Slow Path (Thread-safe):
        # Offload the synchronized load/refresh to a worker thread.
        # This blocks the background worker thread, NOT the asyncio event loop.
        try:
            return await asyncio.to_thread(self._get_and_refresh_token)
        except Exception as e:
            self.logger.error("Failed to retrieve GCP ADC credentials: %s", e)
            raise

    def _get_and_refresh_token(self) -> str:
        with self._lock:
            # Double-Checked Locking: Check again inside the lock
            if self.credentials is None:
                self.logger.info("Initializing GCP Application Default Credentials.")
                credentials, _ = google.auth.default(
                    scopes=["https://www.googleapis.com/auth/cloud-platform"]
                )
                self.credentials = credentials

            if not self.credentials.valid:
                self.logger.info("GCP ADC token is invalid or expired. Refreshing...")
                self.credentials.refresh(Request())

            if not self.credentials.token:
                raise ValueError("GCP ADC token is empty after retrieval/refresh.")

            return self.credentials.token


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
