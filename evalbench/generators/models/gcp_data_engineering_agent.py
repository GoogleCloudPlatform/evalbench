import asyncio
import collections.abc
import concurrent.futures
import datetime
import json
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


def _is_iterable(obj: Any) -> bool:
    """Safely checks if an object is iterable, excluding strings and bytes."""
    if isinstance(obj, (str, bytes)):
        return False
    try:
        iter(obj)
        return True
    except TypeError:
        return False


def _extract_message_text(msg: Any) -> str:
    """Extracts agent message text from a single Message object."""
    if getattr(msg, "role", None) != pb.ROLE_AGENT:
        return ""

    msg_level = ""
    if hasattr(msg, "metadata") and MESSAGE_LEVEL_URI in msg.metadata:
        msg_level = msg.metadata[MESSAGE_LEVEL_URI]

    if msg_level in ["DEBUG", "WARNING", "INFO"]:
        return ""

    text = ""
    if hasattr(msg, "parts"):
        for part in msg.parts:
            if getattr(part, "text", None):
                text += part.text
    return text


def _find_agent_text_recursive(obj: Any) -> str:
    """Recursively searches obj to find and accumulate agent texts."""
    texts = []

    self_text = _extract_message_text(obj)
    if self_text:
        texts.append(self_text)

    # 1. Handle dict-like mappings by traversing their values
    if isinstance(obj, collections.abc.Mapping):
        for val in obj.values():
            text = _find_agent_text_recursive(val)
            if text:
                texts.append(text)
        return "\n\n".join(texts)

    # 2. Handle iterables (exclude string/bytes)
    if _is_iterable(obj):
        for item in obj:
            text = _find_agent_text_recursive(item)
            if text:
                texts.append(text)
        return "\n\n".join(texts)

    # 3. Handle standard Protobuf Messages via ListFields
    elif hasattr(obj, "ListFields"):
        try:
            for field_desc, field_value in obj.ListFields():
                text = _find_agent_text_recursive(field_value)
                if text:
                    texts.append(text)
        except Exception:
            pass
        return "\n\n".join(texts)

    # 4. Fallback for other standard objects
    elif hasattr(obj, "__dict__"):
        for val in obj.__dict__.values():
            text = _find_agent_text_recursive(val)
            if text:
                texts.append(text)
        return "\n\n".join(texts)

    return "\n\n".join(texts)


class DataEngineeringAgentGenerator(QueryGenerator):
    """Data Engineering Agent (DEA) Query Generator using the A2A SDK."""

    def __init__(self, querygenerator_config: dict[str, Any]):
        """Initializes the DataEngineeringAgentGenerator with config.

        Args:
            querygenerator_config: Configuration dictionary containing
              'endpoint' and 'target_workspace' settings.
        """
        super().__init__(querygenerator_config)
        self.name = "data_engineering_agent"
        gcp_project_id = querygenerator_config.get("gcp_project_id", "")
        gcp_region = querygenerator_config.get("gcp_region", "")
        repository = querygenerator_config.get("dataform_repository", "")
        workspace = querygenerator_config.get("dataform_workspace", "")

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
        if not repository:
            raise ValueError(
                "Configuration key 'dataform_repository' is required for "
                "DataEngineeringAgentGenerator."
            )
        if not workspace:
            raise ValueError(
                "Configuration key 'dataform_workspace' is required for "
                "DataEngineeringAgentGenerator."
            )

        self.endpoint = (
            f"https://geminidataanalytics.googleapis.com/v1/a2a/projects/"
            f"{gcp_project_id}/locations/{gcp_region}/"
            f"agents/dataengineeringagent"
        )
        self.target_workspace = (
            f"projects/{gcp_project_id}/locations/{gcp_region}/"
            f"repositories/{repository}/workspaces/{workspace}"
        )

        workspace_chars = (
            self.target_workspace.replace("/", "")
            .replace("-", "")
            .replace("_", "")
        )
        if not workspace_chars.isalnum():
            raise ValueError(
                "Constructed target_workspace path contains invalid "
                f"characters: '{self.target_workspace}'"
            )

        self.auth_interceptor = AuthInterceptor(GcpAdcCredentialService())

        # Cache to maintain conversation-isolated ConversationTokens
        # thread-safely in memory
        self._conversation_token_cache: dict[str, str] = {}
        self._token_lock = threading.Lock()

        logger.info(
            "A2A AuthInterceptor successfully configured with "
            "GcpAdcCredentialService."
        )

    def generate_internal(self, prompt: EvalDeaRequest) -> EvalDeaRequest:
        """Entry point that integrates with DataEngineeringAgentEvaluator."""
        prompt_text = prompt.nl_prompt
        conversation_id = prompt.id

        coro = self._run_client(prompt_text, conversation_id=conversation_id)

        try:
            prompt.generated_nl_response = self.run_async(coro)
        except Exception:
            logger.exception("A2A SDK messaging error")
            raise
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
        security_req = SecurityRequirement()
        security_req.schemes["oauth2"].CopyFrom(StringList(list=[]))
        agent_card.security_requirements.append(security_req)

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

        message_req = pb.SendMessageRequest(
            message=pb.Message(
                message_id=str(uuid.uuid4()),
                role=pb.ROLE_USER,
                context_id=conversation_id,
                parts=[pb.Part(text=prompt)]
            )
        )

        # Configure GCP Resource extension
        message_req.metadata[GCP_RESOURCE_URI] = {
            "gcpResourceId": self.target_workspace
        }

        # Handle ConversationToken state memory thread-safely
        token = ""
        with self._token_lock:
            token = self._conversation_token_cache.get(conversation_id, "")
        if token:
            message_req.metadata[CONVERSATION_TOKEN_URI] = token

        context = ClientCallContext(
            timeout=300.0,
            service_parameters={
                "A2A-Extensions": ALL_EXTENSIONS
            }
        )

        reply_text = ""
        new_token = ""

        try:
            async for resp in client.send_message(
                message_req, context=context
            ):
                extracted_text = self._extract_reply_text(resp)
                if extracted_text:
                    # We use non-streaming mode. The A2A SDK does one call,
                    # gets the complete response, and yields it once.
                    # Overwriting works because the loop runs only once.
                    reply_text = extracted_text

                # Extract Conversation Token
                if (
                    resp.HasField("task")
                    and CONVERSATION_TOKEN_URI in resp.task.metadata
                ):
                    new_token = resp.task.metadata[CONVERSATION_TOKEN_URI]
        except Exception as e:
            self._log_api_error_details(e)
            raise
        finally:
            await client.close()

        # Cache the new token thread-safely
        if new_token:
            with self._token_lock:
                self._conversation_token_cache[conversation_id] = new_token

        return reply_text.strip()

    @staticmethod
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
            ThreadPoolExecutor = concurrent.futures.ThreadPoolExecutor
            with ThreadPoolExecutor(max_workers=1) as executor:
                future = executor.submit(asyncio.run, coro)
                return future.result()
        else:
            return asyncio.run(coro)

    @staticmethod
    def _extract_reply_text(resp: pb.SendMessageResponse) -> str:
        """Extracts user-facing agent response text.

        Filters out internal logs.
        """
        try:
            return _find_agent_text_recursive(resp)
        except Exception as e:
            logger.exception(
                "Failed to parse message text from response: %s", e
            )
            return ""

    @staticmethod
    def _log_api_error_details(e: Exception) -> None:
        """Extracts and logs detailed error messages from API exceptions."""
        try:
            if hasattr(e, "response") and e.response is not None:
                response = e.response
                status_code = getattr(response, "status_code", "unknown")
                response_text = getattr(response, "text", "")

                error_msg = f"API Error (Status {status_code})"
                if response_text:
                    try:
                        body = json.loads(response_text)
                        if isinstance(body, dict):
                            inner_error = body.get("error")
                            if (
                                isinstance(inner_error, dict)
                                and "message" in inner_error
                            ):
                                error_msg += f": {inner_error['message']}"
                            elif "message" in body:
                                error_msg += f": {body['message']}"
                            elif "error_description" in body:
                                error_msg += f": {body['error_description']}"
                            else:
                                error_msg += f": {response_text}"
                        else:
                            error_msg += f": {response_text}"
                    except json.JSONDecodeError:
                        error_msg += f": {response_text}"
                logger.exception(error_msg)
                return

            logger.exception("API Unexpected connection error: %s", e)
        except Exception as parse_err:
            logger.exception(
                "Failed to parse API error details: %s", parse_err
            )
