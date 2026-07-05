"""Utility functions to aid the scorers."""

from typing import Any
import logging
import hashlib
import pickle
from util.safe_pickle import safe_pickle_loads


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
            return safe_pickle_loads(cached_result)
    except Exception as e:
        logging.warning(f"Failed to retrieve query from cache: {e}")

    # Execute the method as the result is not cached
    try:
        response = execution_method(prompt)
    except Exception as e:
        logging.error(
            f"Execution method failed for prompt: {prompt} with error: {e}")
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
        return tuple(make_hashable(v) for v in value)
    elif isinstance(value, dict):
        return frozenset((k, make_hashable(v)) for k, v in value.items())
    return value


def filter_conversation_history_json(
    history: Any, include_tool_calls: bool = False
) -> str:
    """Filters the conversation history JSON to include or exclude tool_calls.

    Maintains the exact JSON list structure expected by downstream LLM prompts.
    """
    import json

    if isinstance(history, str):
        try:
            history_list = json.loads(history)
        except (json.JSONDecodeError, TypeError):
            return history
    elif isinstance(history, list):
        history_list = history
    else:
        return str(history)

    cleaned_history = []
    for turn in history_list:
        if not isinstance(turn, dict):
            cleaned_history.append(turn)
            continue

        user_msg = turn.get("user", "")
        agent_raw = turn.get("agent", "")

        agent_val = agent_raw
        if isinstance(agent_raw, str):
            try:
                parsed = json.loads(agent_raw)
                if isinstance(parsed, dict):
                    if not include_tool_calls and "tool_calls" in parsed:
                        parsed.pop("tool_calls", None)
                    agent_val = json.dumps(parsed)
            except (json.JSONDecodeError, TypeError):
                # If agent_raw is not valid JSON, keep the original string unchanged.
                agent_val = agent_raw
        elif isinstance(agent_raw, dict):
            parsed = dict(agent_raw)
            if not include_tool_calls and "tool_calls" in parsed:
                parsed.pop("tool_calls", None)
            agent_val = json.dumps(parsed)

        cleaned_history.append({
            "user": user_msg,
            "agent": agent_val
        })

    return json.dumps(cleaned_history, indent=2)
