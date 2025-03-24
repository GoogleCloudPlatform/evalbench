import logging
import time

from threading import Semaphore
from typing import Tuple, Any


class ResourceExhaustedError(Exception):
    pass


def rate_limit(
    query: Tuple,
    execution_method,
    execs_per_minute: int,
    semaphore: Semaphore,
    max_attempts: int,
) -> Any:
    # If no limit is specified, run immediately.
    if not isinstance(execs_per_minute, int):
        return execution_method(*query)

    result = None
    semaphore.acquire()
    attempt = 1
    while attempt <= max_attempts:
        try:
            result = execution_method(*query)
            break
        except Exception as e:
            logging.info(e)
            # exponentially backoff starting at 5 seconds
            time.sleep(5 * (2 ** (attempt)))
            attempt += 1
    time.sleep(60 / execs_per_minute)
    semaphore.release()
    return result
