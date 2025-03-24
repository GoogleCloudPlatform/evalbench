from abc import ABC, abstractmethod


class QueryGenerator(ABC):

    def __init__(self, promptgenerator_config):
        pass

    @abstractmethod
    def generate(self, prompt):
        raise NotImplementedError("Subclasses must implement this method")
