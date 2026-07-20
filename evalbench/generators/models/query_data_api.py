from .generator import QueryGenerator
import google.cloud.geminidataanalytics_v1beta as gda
import google.auth
import google.auth.transport.requests
import logging
import requests
from typing import Dict, Any
from google.api_core.exceptions import (
    ResourceExhausted,
    ServiceUnavailable,
    DeadlineExceeded,
)
from util.rate_limit import ResourceExhaustedError

# Per-call gRPC deadline in seconds. The API may take a while to respond, but
# it must be bounded so a stalled server cannot block evaluation indefinitely.
_REQUEST_TIMEOUT_SECONDS = 300.0

# Default Production API Endpoint for Gemini Data Analytics
_DEFAULT_API_ENDPOINT = "geminidataanalytics.googleapis.com"

# Shared default generation options settings
_DEFAULT_GENERATION_OPTIONS = {
    "generate_query_result": True,
    "generate_natural_language_answer": False,
    "generate_explanation": True,
    "generate_disambiguation_question": True,
}


def _to_camel_case_dict(val: Any) -> Any:
    """Recursively converts snake_case keys in dicts/lists to lowerCamelCase for REST payloads."""
    if isinstance(val, dict):
        res = {}
        for key, value in val.items():
            if isinstance(key, str) and "_" in key:
                parts = [p for p in key.split("_") if p]
                if parts:
                    camel_key = parts[0].lower() + "".join(p.capitalize() for p in parts[1:])
                    if key.startswith("_"):
                        camel_key = "_" + camel_key
                else:
                    camel_key = key
            else:
                camel_key = key
            res[camel_key] = _to_camel_case_dict(value)
        return res
    elif isinstance(val, (list, tuple)):
        return [_to_camel_case_dict(item) for item in val]
    return val


def _format_result(
    generated_sql: Any,
    intent_explanation: Any,
    disambiguation_questions: Any,
) -> Dict[str, Any]:
    """Formats raw API response fields into standard result format."""
    return {
        "generated_sql": generated_sql,
        "other": {
            "intent_explanation": (
                intent_explanation or ""
            ),
            "disambiguation_question": list(
                disambiguation_questions or []
            ),
        },
    }


class QueryDataAPIGenerator(QueryGenerator):
    """
    Generator that calls the Google Cloud Gemini Data Analytics API
    (Query Data) to get SQL suggestions and metadata.
    """

    def __init__(self, querygenerator_config: Dict[str, Any]):
        super().__init__(querygenerator_config)
        self.name = "query_data_api"
        self.project_id = querygenerator_config.get("project_id")
        self.location = querygenerator_config.get("location", "global")
        self.context = querygenerator_config.get("context", {})
        self.use_rest_api = querygenerator_config.get("use_rest_api", False)
        self.api_endpoint = (
            querygenerator_config.get("api_endpoint")
            or _DEFAULT_API_ENDPOINT
        )

        # Initialize client
        # Authenticated via ADC automatically
        client_options = {"api_endpoint": self.api_endpoint}
        self.client = gda.DataChatServiceClient(
            client_options=client_options
        )

    def generate_internal(self, prompt: str) -> Dict[str, Any]:
        """
        Generates SQL for the given prompt using the QueryData API.

        If use_rest_api is True, uses GDA REST HTTP API directly.
        Otherwise (default), uses GDA gRPC client library.
        """
        if self.use_rest_api:
            return self._query_data_rest(prompt)
        return self._query_data_client(prompt)

    def _query_data_client(self, prompt: str) -> Dict[str, Any]:
        """
        Executes query via GDA gRPC client library.
        """
        logger = logging.getLogger(__name__)
        try:
            parent = f"projects/{self.project_id}/locations/{self.location}"
            context_obj = gda.QueryDataContext(**self.context)
            gen_options = gda.GenerationOptions(**_DEFAULT_GENERATION_OPTIONS)

            request = gda.QueryDataRequest(
                parent=parent,
                prompt=prompt,
                context=context_obj,
                generation_options=gen_options
            )

            logger.info(
                f"Invoking QueryData API for project {self.project_id}"
            )
            response = self.client.query_data(
                request=request, timeout=_REQUEST_TIMEOUT_SECONDS
            )

            return _format_result(
                getattr(response, "generated_query", None),
                getattr(response, "intent_explanation", ""),
                getattr(response, "disambiguation_questions", []),
            )

        except (ResourceExhausted, ServiceUnavailable, DeadlineExceeded) as e:
            raise ResourceExhaustedError(e)
        except Exception as e:
            logger.exception("Unhandled exception during QueryData API call")
            raise

    def _query_data_rest(self, prompt: str) -> Dict[str, Any]:
        """
        Fallback REST HTTP execution path for unreleased/private fields
        missing from PyPI SDK protos.
        """
        logger = logging.getLogger(__name__)
        credentials, _ = google.auth.default(
            scopes=["https://www.googleapis.com/auth/cloud-platform"]
        )
        if not credentials.valid:
            auth_req = google.auth.transport.requests.Request()
            credentials.refresh(auth_req)

        url = (
            f"https://{self.api_endpoint}/v1beta/projects/{self.project_id}"
            f"/locations/{self.location}:queryData"
        )

        payload = {
            "prompt": prompt,
            "context": _to_camel_case_dict(self.context),
            "generationOptions": _to_camel_case_dict(
                _DEFAULT_GENERATION_OPTIONS
            ),
        }

        headers = {
            "Authorization": f"Bearer {credentials.token}",
            "Content-Type": "application/json",
        }

        logger.info(f"Invoking GDA REST API at {url}")
        resp = requests.post(
            url,
            json=payload,
            headers=headers,
            timeout=_REQUEST_TIMEOUT_SECONDS
        )

        if resp.status_code in (429, 503, 504):
            raise ResourceExhaustedError(
                f"GDA REST API rate limited / unavailable ({resp.status_code})"
            )

        resp.raise_for_status()
        data = resp.json()

        return _format_result(
            data.get("generatedQuery"),
            data.get("intentExplanation"),
            data.get("disambiguationQuestion"),
        )
