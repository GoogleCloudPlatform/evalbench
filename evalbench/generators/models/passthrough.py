from .generator import QueryGenerator


class NOOPGenerator(QueryGenerator):

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)

    def generate(self, human_language_question):
        return human_language_question
