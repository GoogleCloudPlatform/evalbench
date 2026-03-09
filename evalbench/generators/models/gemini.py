from google import genai
from google.genai.types import GenerateContentResponse
from util.rate_limit import ResourceExhaustedError
from util.gcp import get_gcp_project, get_gcp_region
from google.api_core.exceptions import ResourceExhausted
from .generator import QueryGenerator
from util.sanitizer import sanitize_sql
import logging


class GeminiGenerator(QueryGenerator):
    """Generator queries using Vertex model."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "gcp_vertex_gemini"
        self.project_id = get_gcp_project(
            querygenerator_config.get("gcp_project_id"))
        self.region = get_gcp_region(querygenerator_config.get("gcp_region"))
        self.vertex_model = querygenerator_config["vertex_model"]
        self.base_prompt = querygenerator_config.get("base_prompt") or ""
        self.generation_config = None

        self.client = genai.Client(
            vertexai=True, project=self.project_id, location=self.region
        )
        self.base_prompt = self.base_prompt

    def generate_internal(self, prompt):
        logger = logging.getLogger(__name__)
        try:
            response = self.client.models.generate_content(
                model=self.vertex_model,
                contents=self.base_prompt + prompt,
            )
            if isinstance(response, GenerateContentResponse):
                r = sanitize_sql(response.text)
            return r
        except ResourceExhausted as e:
            raise ResourceExhaustedError(e)
        except Exception as e:
            logger.exception("Unhandled exception during generate_content")
            raise
