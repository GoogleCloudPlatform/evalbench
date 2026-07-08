from .generator import QueryGenerator
import google.cloud.geminidataanalytics_v1beta as gda
import google.auth
import google.auth.transport.requests
import logging
import requests
from typing import Dict, Any
from google.api_core.client_options import ClientOptions
from google.api_core.exceptions import ResourceExhausted, ServiceUnavailable, DeadlineExceeded
from util.rate_limit import ResourceExhaustedError

# Per-call gRPC deadline in seconds. The API may take a while to respond, but
# it must be bounded so a stalled server cannot block evaluation indefinitely.
_REQUEST_TIMEOUT_SECONDS = 300.0

# Default Production API Endpoint for Gemini Data Analytics
_DEFAULT_API_ENDPOINT = "geminidataanalytics.googleapis.com"

# Exceptions caused by unreleased proto fields failing SDK serialization
_PROTO_SERIALIZATION_ERRORS = (AttributeError, ValueError, TypeError)


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
        if querygenerator_config.get("api_endpoint"):
            client_options = {"api_endpoint": self.api_endpoint}
            self.client = gda.DataChatServiceClient(
                client_options=client_options
            )
        else:
            self.client = gda.DataChatServiceClient()

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

            # Map context to QueryDataContext proto message
            # Modern Google SDKs generally support dict initialization
            # for nested messages
            context_obj = gda.QueryDataContext(**self.context)

            gen_options = gda.GenerationOptions(
                generate_query_result=True,
                generate_natural_language_answer=False,
                generate_explanation=True,
                generate_disambiguation_question=True
            )

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

            # Extract fields safely
            generated_sql = getattr(response, "generated_query", None)

            # intent_explanation and disambiguation_questions
            # Depending on response version, these might be lists or strings
            intent_explanation = getattr(response, "intent_explanation", "")
            disambiguation_questions = getattr(
                response, "disambiguation_questions", [])

            result = {
                "generated_sql": generated_sql,
                "other": {
                    "intent_explanation": intent_explanation,
                    "disambiguation_question": list(disambiguation_questions)
                }
            }
            return result

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
            "context": self.context,
            "generationOptions": {
                "generateQueryResult": True,
                "generateNaturalLanguageAnswer": False,
                "generateExplanation": True,
                "generateDisambiguationQuestion": True
            }
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

        generated_sql = data.get("generatedQuery")
        intent_explanation = data.get("intentExplanation", "")
        disambiguation_questions = data.get("disambiguationQuestion", [])

        return {
            "generated_sql": generated_sql,
            "other": {
                "intent_explanation": intent_explanation,
                "disambiguation_question": list(disambiguation_questions)
            }
        }
