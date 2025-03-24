from abc import ABC, abstractmethod
from util.rate_limit import rate_limit
from threading import Semaphore


class QueryGenerator(ABC):

    def __init__(self, querygenerator_config):
        self.execs_per_minute = querygenerator_config.get("execs_per_minute") or 60
        self.max_attempts = querygenerator_config.get("max_attempts") or 3
        self.semaphore = Semaphore(self.execs_per_minute)

    def generate(self, prompt):
        return rate_limit(
            (prompt,),
            self.generate_internal,
            self.execs_per_minute,
            self.semaphore,
            self.max_attempts,
        )

    @abstractmethod
    def generate_internal(self, prompt):
        raise NotImplementedError("Subclasses must implement this method")
