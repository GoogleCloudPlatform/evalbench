from .generator import PromptGenerator


class NOOPGenerator(PromptGenerator):

    def __init__(self, db, promptgenerator_config):
        super().__init__(db, promptgenerator_config)

    def setup(self):
        pass

    def generate(self, prompt):
        return prompt
