from abc import ABC, abstractmethod
import logging
from util.rate_limit import rate_limit, ResourceExhaustedError
from threading import Semaphore


class QueryGenerator(ABC):

    def __init__(self, querygenerator_config):
        self.execs_per_minute = querygenerator_config.get(
            "execs_per_minute") or None
        self.max_attempts = querygenerator_config.get("max_attempts") or 3
        self.semaphore = Semaphore(self.execs_per_minute or 1)

    def generate(self, prompt):
        try:
            return rate_limit(
                (prompt,),
                self.generate_internal,
                self.execs_per_minute,
                self.semaphore,
                self.max_attempts,
            )
        except ResourceExhaustedError as e:
            logging.info(
                "Resource Exhausted after multiple attempts on Generation."
                + "Giving up. Try reducing execs_per_minute."
            )
            return ""

    @abstractmethod
    def generate_internal(self, prompt):
        raise NotImplementedError("Subclasses must implement this method")
