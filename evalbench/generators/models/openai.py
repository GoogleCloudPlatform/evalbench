import time
from openai import OpenAI
from .generator import QueryGenerator


class OAIQueryGenerator(QueryGenerator):

    def __init__(self, core_db, querygenerator_config):
        super().__init__(core_db, querygenerator_config)
        self.name = "gpt-4-0125-preview"
        self.base_prompt = querygenerator_config["base_prompt"]
        self.name = "openai"

    def generate(self, prompt: str) -> str:
        start_time = time.time()
        prompt = self.base_prompt + prompt
        client = OpenAI()
        completion = client.chat.completions.create(
            model="gpt-4-0125-preview",
            messages=[
                {"role": "user", "content": prompt},
                {"role": "assistant", "content": ""},
            ],
            temperature=0.0,
        )
        return str(completion.choices[0].message.content)
