from .generator import QueryGenerator
import google.cloud.geminidataanalytics_v1beta as gda
import logging
from typing import Dict, Any


class QueryDataAPIGenerator(QueryGenerator):
    """
    Generator that calls the Google Cloud Gemini Data Analytics API (Query Data)
    to get SQL suggestions and metadata.
    """

    def __init__(self, querygenerator_config: Dict[str, Any]):
        super().__init__(querygenerator_config)
        self.name = "query_data_api"
        self.project_id = querygenerator_config.get("project_id")
        self.location = querygenerator_config.get("location", "global")
        self.context = querygenerator_config.get("context", {})

        # Initialize client
        # Authenticated via ADC automatically
        self.client = gda.DataChatServiceClient()

    def generate_internal(self, prompt: str) -> Dict[str, Any]:
        """
        Generates SQL for the given prompt using the QueryData API.

        Args:
            prompt: The natural language question.

        Returns:
            A dictionary with generated_sql and rich metadata.
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

            logger.info(f"Invoking QueryData API for project {self.project_id}")
            response = self.client.query_data(request=request)

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

        except Exception as e:
            logger.exception("Unhandled exception during QueryData API call")
            raise
