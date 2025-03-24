from abc import ABC, abstractmethod


class PromptGenerator(ABC):

    def __init__(self, db_config, promptgenerator_config):
        pass

    @abstractmethod
    def generate(self, prompt):
        raise NotImplementedError("Subclasses must implement this method")
    
    @abstractmethod
    # method is called once the database is ready and generation is about to begin. 
    # This should prepare anything generation module needs such as caching the schema, etc.
    def setup(self):
        pass
