"""Utility functions to aid the scorers."""

from typing import Any
import json
import logging
import hashlib
import pickle
import re
from util.safe_pickle import safe_pickle_loads


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response (handles code fences)."""
    if not text:
        raise ValueError("empty model response")
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    candidates = [text]
    # Fall back to the outermost {...} span for prose-wrapped responses.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        candidates.append(text[start:end + 1])
    for candidate in candidates:
        try:
            parsed = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        # A bare array or scalar parses fine but breaks every caller's .get().
        if isinstance(parsed, dict):
            return parsed
    raise ValueError("no JSON object found in model response")


# Gemini 3.x draws reasoning tokens from the output budget, so a high cap keeps
# thought tokens from starving the JSON answer (which otherwise finishes with
# empty ``.text``).
_MAX_OUTPUT_TOKENS = 65536

# genai defaults to no request timeout, so a stalled call blocks its thread
# forever. Generous enough for a whole-dataset thinking-model call, but bounded
# so one bad request degrades to a dropped metric instead of hanging the run.
_REQUEST_TIMEOUT_MS = 10 * 60 * 1000


def _finish_reason(resp) -> str:
    try:
        return str(resp.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        return "unknown"


def supports_json_mode(model) -> bool:
    """Returns True if `model` exposes the genai client that JSON mode needs."""
    return (getattr(model, "client", None) is not None
            and callable(getattr(model, "_call_generate_content", None)))


def generate_json(model, prompt: str, response_schema: dict | None = None,
                  max_output_tokens: int | None = _MAX_OUTPUT_TOKENS) -> str:
    """Generate a model response as raw JSON text.

    Uses Gemini's native JSON mode via the underlying genai client, which bypasses
    ``GeminiGenerator.generate``'s SQL sanitizer (it corrupts JSON escapes). Only
    non-Gemini models fall back to ``model.generate``. ``max_output_tokens=None``
    uses the model's default limit.
    """
    if supports_json_mode(model):
        caller = model._call_generate_content
        try:
            from google.genai import types

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=max_output_tokens,
                response_schema=response_schema,
                http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
            )
            resp = caller(contents=prompt, config=config)
        except Exception as e:
            logging.warning("generate_json: JSON-mode generation failed (%s)", e)
            return ""
        text = getattr(resp, "text", None)
        if text:
            return text
        logging.warning(
            "generate_json: JSON-mode returned empty text (finish_reason=%s)",
            _finish_reason(resp),
        )
        return ""
    return model.generate(prompt)


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
    try:
        hash(value)
        return value
    except TypeError:
        return str(value)


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
