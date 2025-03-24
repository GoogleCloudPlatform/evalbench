import logging
from anthropic import AnthropicVertex
from util.gcp import get_gcp_project, get_gcp_region
from .generator import QueryGenerator


class ClaudeGenerator(QueryGenerator):
    """Generate queries using the Anthropic Claude model via Vertex AI."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "gcp_vertex_claude"
        self.project_id = get_gcp_project(querygenerator_config.get("gcp_project_id"))
        self.region = get_gcp_region(querygenerator_config.get("gcp_region"))
        self.model_id = querygenerator_config["vertex_model"]
        self.base_prompt = querygenerator_config["base_prompt"]
        self.max_tokens = querygenerator_config["max_tokens"]

        self.client = AnthropicVertex(region=self.region, project_id=self.project_id)

    def generate_internal(self, prompt):

        try:
            response = self.client.messages.create(
                model=self.model_id,
                messages=[
                    {
                        "role": "user",
                        "content": self.base_prompt + prompt,
                    }
                ],
                max_tokens=self.max_tokens,
                temperature=0,
            )

            r = response.content[0].text if response.content else ""
            return r

        except Exception as e:
            logging.error(f"Error generating response from Claude: {e}")
            return None
