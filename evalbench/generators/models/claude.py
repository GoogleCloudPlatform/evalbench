import logging
from anthropic import AnthropicVertex

from .generator import QueryGenerator


class ClaudeGenerator(QueryGenerator):
    """Generate queries using the Anthropic Claude model via Vertex AI."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "claude"
        self.project_id = querygenerator_config["project_id"]
        self.location = querygenerator_config["location"]
        self.model_id = querygenerator_config["vertex_model"]
        self.base_prompt = querygenerator_config["base_prompt"]
        self.max_tokens = querygenerator_config["max_tokens"]

        self.client = AnthropicVertex(region=self.location, project_id=self.project_id)

    def generate(self, prompt):

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
