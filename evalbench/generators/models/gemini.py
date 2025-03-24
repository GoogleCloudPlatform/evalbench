import vertexai

from vertexai.generative_models._generative_models import (
    GenerativeModel,
    GenerationResponse,
)
from .generator import QueryGenerator


class GeminiGenerator(QueryGenerator):
    """Generator queries using Vertex model."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "gcp_vertex_gemini"
        self.project_id = querygenerator_config["gcp_project_id"]
        self.region = querygenerator_config["gcp_region"]
        self.vertex_model = querygenerator_config["vertex_model"]
        self.base_prompt = querygenerator_config["base_prompt"]
        self.generation_config = None

        vertexai.init(project=self.project_id, location=self.region)
        self.model = GenerativeModel(self.vertex_model)
        self.base_prompt = self.base_prompt

    def generate_internal(self, prompt):
        response = self.model.generate_content(
            self.base_prompt + prompt,
            generation_config=self.generation_config,
        )
        if isinstance(response, GenerationResponse):
            r = response.text
            r = r.replace(
                "```sql", ""
            )  # required for gemini_1.0_pro, gemini_2.0_flash, gemini_2.0_pro
        return r
