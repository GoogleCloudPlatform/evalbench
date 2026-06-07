import asyncio
import concurrent.futures
import logging
import threading
import uuid
from typing import Any, Coroutine

from a2a.client import (
    create_client,
    ClientConfig,
    ClientCallContext,
    minimal_agent_card,
)
from a2a.utils import TransportProtocol
from a2a.client.auth import AuthInterceptor, CredentialService
from a2a.types import (
    SecurityRequirement,
    SecurityScheme,
    OAuth2SecurityScheme,
    StringList,
    a2a_pb2 as pb,
)
import google.auth
from google.auth.exceptions import DefaultCredentialsError, RefreshError
from google.auth.transport.requests import Request

from .generator import QueryGenerator
from dataset.dataengineeringagentinput import EvalDeaRequest
# Standardized A2A Extension URIs
CONVERSATION_TOKEN_URI = (
    "https://geminidataanalytics.googleapis.com/a2a/extensions/"
    "conversationtoken/v1"
)
GCP_RESOURCE_URI = (
    "https://geminidataanalytics.googleapis.com/a2a/extensions/"
    "gcpresource/v1"
)
MESSAGE_LEVEL_URI = (
    "https://geminidataanalytics.googleapis.com/a2a/extensions/"
    "messagelevel/v1"
)
INSTRUCTION_URI = (
    "https://geminidataanalytics.googleapis.com/a2a/extensions/"
    "instruction/v1"
)
FINISH_REASON_URI = (
    "https://geminidataanalytics.googleapis.com/a2a/extensions/"
    "finishreason/v1"
)

# All required A2A Extension Headers combined
ALL_EXTENSIONS = (
    f"{MESSAGE_LEVEL_URI},{INSTRUCTION_URI},{GCP_RESOURCE_URI},"
    f"{CONVERSATION_TOKEN_URI},{FINISH_REASON_URI}"
)

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


def run_async(coro: Coroutine[Any, Any, Any]) -> Any:
    """Safely runs an async coroutine in a synchronous context.

    Handles cases where an event loop is already running (e.g., in Jupyter
    notebooks or nested async environments) by offloading to a thread.
    """
    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    if loop is not None and loop.is_running():
        # Run in a separate thread to avoid "Event loop is already running"
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(asyncio.run, coro)
            # Automatically raises exceptions if they occurred
            return future.result()
    else:
        return asyncio.run(coro)


def _extract_reply_text(resp: pb.SendMessageResponse) -> str:
    """Extracts user-facing agent response text.

    Filters out internal logs.
    """
    try:
        reply_text = ""
        if not (resp.HasField("task") and resp.task.history):
            return reply_text

        for msg in resp.task.history:
            if msg.role == pb.ROLE_AGENT:
                msg_level = ""
                if MESSAGE_LEVEL_URI in msg.metadata:
                    msg_level = msg.metadata[MESSAGE_LEVEL_URI]
                if msg_level not in ["DEBUG", "WARNING", "INFO"]:
                    for part in msg.parts:
                        if part.text:
                            reply_text += part.text
        return reply_text
    except Exception as e:
        logger.exception("Failed to parse message text from response: %s", e)
        return ""


class DataEngineeringAgentGenerator(QueryGenerator):
    """Data Engineering Agent (DEA) Query Generator using the A2A SDK."""

    name: str
    endpoint: str
    target_workspace: str
    auth_interceptor: AuthInterceptor
    _conversation_token_cache: dict[str, str]

    def __init__(self, querygenerator_config: dict[str, Any]) -> None:
        """Initializes the DataEngineeringAgentGenerator with config.

        Args:
            querygenerator_config: Configuration dictionary containing
              'endpoint' and 'target_workspace' settings.
        """
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

        # Cache to maintain conversation-isolated ConversationTokens
        # thread-safely in memory
        self._conversation_token_cache = {}
        self._token_lock = threading.Lock()

        logger.info(
            "A2A AuthInterceptor successfully configured with "
            "GcpAdcCredentialService."
        )

    def generate_internal(self, prompt: EvalDeaRequest) -> EvalDeaRequest:
        """Entry point that integrates with DEAEvaluator."""
        prompt_text = prompt.nl_prompt
        conversation_id = prompt.id

        coro = self._run_client(prompt_text, conversation_id=conversation_id)

        try:
            prompt.generated_nl_response = run_async(coro)
        except Exception as e:
            logger.exception("A2A SDK messaging error")
            prompt.generated_nl_response = ""
        return prompt

    async def _run_client(
        self, prompt: str, conversation_id: str | None
    ) -> str:
        """Core asynchronous A2A SDK connection loop."""
        # Configure Client in standard Non-Streaming Mode
        config = ClientConfig(
            supported_protocol_bindings=[
                TransportProtocol.HTTP_JSON,
            ],
            streaming=False
        )

        agent_card = minimal_agent_card(
            self.endpoint,
            transports=[TransportProtocol.HTTP_JSON]
        )
        req = SecurityRequirement()
        req.schemes["oauth2"].CopyFrom(StringList(list=[]))
        agent_card.security_requirements.append(req)

        scheme = SecurityScheme(
            oauth2_security_scheme=OAuth2SecurityScheme(
                description="OAuth2 for GCP authentication"
            )
        )
        agent_card.security_schemes["oauth2"].CopyFrom(scheme)

        # Enforce legacy v0.3 protocol version to trigger compatibility
        # transport layers
        for interface in agent_card.supported_interfaces:
            interface.protocol_version = "0.3"

        client = await create_client(
            agent_card,
            client_config=config,
            interceptors=[self.auth_interceptor]
        )

        if not conversation_id:
            conversation_id = f"conv-{uuid.uuid4()}"

        req = pb.SendMessageRequest(
            message=pb.Message(
                message_id=str(uuid.uuid4()),
                role=pb.ROLE_USER,
                context_id="live-dea-workflow",
                parts=[pb.Part(text=prompt)]
            )
        )

        # Configure GCP Resource extension
        req.metadata.update({
            GCP_RESOURCE_URI: {
                "gcpResourceId": self.target_workspace
            }
        })

        # Handle ConversationToken state memory thread-safely
        token = ""
        with self._token_lock:
            token = self._conversation_token_cache.get(conversation_id, "")
        if token:
            req.metadata[CONVERSATION_TOKEN_URI] = token

        context = ClientCallContext(
            timeout=180.0,
            service_parameters={
                "A2A-Extensions": ALL_EXTENSIONS
            }
        )

        reply_text = ""
        new_token = ""

        try:
            async for resp in client.send_message(req, context=context):
                extracted_text = _extract_reply_text(resp)
                if extracted_text:
                    reply_text = extracted_text

                # Extract Conversation Token
                if (
                    resp.HasField("task")
                    and CONVERSATION_TOKEN_URI in resp.task.metadata
                ):
                    new_token = resp.task.metadata[CONVERSATION_TOKEN_URI]
        except Exception as e:
            logger.exception("A2A SDK messaging error")
            raise

        await client.close()

        # Cache the new token thread-safely
        if new_token:
            with self._token_lock:
                self._conversation_token_cache[conversation_id] = new_token

        return reply_text.strip()
