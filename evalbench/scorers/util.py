"""Utility functions to aid the scorers."""

from typing import Any
import logging
import hashlib
import pickle


def with_cache_execute(
    prompt: str,
    model: str,
    execution_method: Any,
    cache_client: Any,
) -> Any:
    """
    Execute a task with caching support. If the result exists in the cache,
    it retrieves it; otherwise, it executes the method and caches the result.

    Args:
        prompt (str): The input prompt for the execution.
        model (str): The model identifier.
        execution_method (Callable[[str], Any]): The method to execute if the result is not cached.
        cache_client (Any): The caching client (e.g., Redis).

    Returns:
        Any: The execution result.
    """
    # Generate a hash of the prompt and model
    query_hash = hashlib.sha256((prompt + model).encode()).hexdigest()

    # Attempt to retrieve from cache
    try:
        cached_result = cache_client.get(query_hash)
        if cached_result is not None:  # Ensure the result is valid
            logging.debug("Found cached result for comparing prompt")
            return pickle.loads(cached_result)
    except Exception as e:
        logging.warning(f"Failed to retrieve query from cache: {e}")

    # Execute the method as the result is not cached
    try:
        response = execution_method(prompt)
    except Exception as e:
        logging.error(f"Execution method failed for prompt: {prompt} with error: {e}")
        return None

    # Attempt to cache the result
    try:
        cache_client.set(query_hash, pickle.dumps(response))
        logging.debug(f"Cached result for prompt")
    except Exception as e:
        logging.warning(f"Failed to cache query result: {e}")

    return response


def make_hashable(value):
    if isinstance(value, list):
        return tuple(value)
    elif isinstance(value, dict):
        return frozenset((k, make_hashable(v)) for k, v in value.items())
    return value
