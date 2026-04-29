from google import genai
from google.genai.types import GenerateContentResponse
from util.rate_limit import ResourceExhaustedError
from util.gcp import get_gcp_project, get_gcp_region
from google.api_core.exceptions import ResourceExhausted
from .generator import QueryGenerator
from util.sanitizer import sanitize_sql
import logging
import os


class GeminiGenerator(QueryGenerator):
    """Generator queries using Vertex model."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "gcp_vertex_gemini"
        self.project_id = get_gcp_project(
            querygenerator_config.get("gcp_project_id"))
        self.region = get_gcp_region(querygenerator_config.get("gcp_region"))
        if not self.project_id or not self.region:
            # Attempt to use GEMINI_API_KEY for authentication
            api_key = os.environ.get("GEMINI_API_KEY")
            if not api_key:
                raise ValueError("Both gcp_project_id and gcp_region must be set in config when GEMINI_API_KEY is not available.")
            self.client = genai.Client(api_key=api_key)
        else:
            self.client = genai.Client(
                vertexai=True, project=self.project_id, location=self.region
            )

        self.vertex_model = querygenerator_config["vertex_model"]
        self.base_prompt = querygenerator_config.get("base_prompt") or ""
        self.generation_config = None
        self.base_prompt = self.base_prompt

    def generate_internal(self, prompt):
        import time
        from google.genai.errors import ClientError
        logger = logging.getLogger(__name__)
        for attempt in range(5):
            try:
                response = self.client.models.generate_content(
                    model=self.vertex_model,
                    contents=self.base_prompt + prompt,
                )
                if isinstance(response, GenerateContentResponse):
                    r = sanitize_sql(response.text)
                return r
            except ResourceExhausted as e:
                if attempt < 4:
                    logger.warning(f"ResourceExhausted. Retrying attempt {attempt + 1} after sleep...")
                    time.sleep(2 ** attempt * 2)
                else:
                    raise ResourceExhaustedError(e)
            except genai.errors.ClientError as e:
                if "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e):
                    if attempt < 4:
                        logger.warning(f"429/RESOURCE_EXHAUSTED. Retrying attempt {attempt + 1} after sleep...")
                        time.sleep(2 ** attempt * 2)
                    else:
                        raise ResourceExhaustedError(e)
                else:
                    raise
            except Exception as e:
                logger.exception("Unhandled exception during generate_content")
                raise
