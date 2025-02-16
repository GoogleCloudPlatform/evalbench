from .generator import QueryGenerator


class NOOPGenerator(QueryGenerator):

    def __init__(self, core_db, querygenerator_config):
        super().__init__(core_db, querygenerator_config)
        self.name = "noop"

    def generate(self, prompt):
        return ''
