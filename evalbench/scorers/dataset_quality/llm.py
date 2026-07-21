"""Shared LLM plumbing for the dataset-quality judge scorers.

Each LLM scorer owns its own prompt and call but shares this low-level plumbing:
JSON-mode generation, defensive JSON extraction, and tag alignment by CUJ id.
Mirrors ``scorers.mcp_style_readability`` so a slightly malformed model response
degrades gracefully instead of crashing the run.
"""

import json
import logging
import re


# Generous output cap so a thinking model's reasoning tokens don't starve the
# JSON answer. Gemini 3.x models think by default and draw reasoning tokens from
# the output budget; without an explicit, high cap the response can finish
# (finish_reason=MAX_TOKENS) with only thought parts and empty ``.text``. 65536
# is the max output for the target Gemini Pro models, so it is safe to set for
# both 2.5 (non-truncating) and 3.x (needs the headroom).
_MAX_OUTPUT_TOKENS = 65536


def _finish_reason(resp) -> str:
    try:
        return str(resp.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        return "unknown"


def generate_json(model, prompt: str) -> str:
    """Generate a model response as raw JSON text.

    Prefer Gemini's native JSON mode via the underlying genai client: it
    guarantees valid JSON and bypasses ``GeminiGenerator.generate``'s SQL
    sanitizer, which corrupts JSON escapes. Crucially, on the Gemini path an
    empty response is NOT routed through ``model.generate()`` -- that fallback
    runs the SQL sanitizer and turns an already-failed call into corrupted JSON.
    An empty/failed Gemini call returns "" so callers degrade to their safe
    default. Only genuinely non-Gemini models (no native JSON mode) use
    ``model.generate``.
    """
    client = getattr(model, "client", None)
    caller = getattr(model, "_call_generate_content", None)
    if client is not None and callable(caller):
        try:
            from google.genai import types

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
                max_output_tokens=_MAX_OUTPUT_TOKENS,
            )
            resp = caller(contents=prompt, config=config)
        except Exception as e:
            logging.warning(
                "dataset_quality: JSON-mode generation failed (%s)", e
            )
            return ""
        text = getattr(resp, "text", None)
        if text:
            return text
        logging.warning(
            "dataset_quality: JSON-mode returned empty text "
            "(finish_reason=%s); not falling back to the SQL-sanitizing "
            "generate() path.",
            _finish_reason(resp),
        )
        return ""
    return model.generate(prompt)


def extract_json(text: str) -> dict:
    """Pull a JSON object out of a model response (handles code fences)."""
    if not text:
        raise ValueError("empty model response")
    text = text.strip()
    fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
    if fence:
        text = fence.group(1).strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end > start:
            try:
                return json.loads(text[start:end + 1])
            except json.JSONDecodeError:
                pass
        raise ValueError("no JSON object found in model response")


def tag_cujs(model, prompt: str) -> dict[str, dict]:
    """Run one tagging prompt and return the per-CUJ tags indexed by id.

    Returns ``{}`` on any parse failure so a malformed judge response yields
    all-missing tags (each scorer then treats missing as its safe default)
    rather than aborting the run.
    """
    try:
        data = extract_json(generate_json(model, prompt))
    except ValueError as e:
        logging.warning("dataset_quality: could not parse judge response: %s", e)
        return {}
    tags = data.get("tags")
    if not isinstance(tags, list):
        return {}
    indexed = {}
    for tag in tags:
        if isinstance(tag, dict) and tag.get("id") is not None:
            indexed[tag["id"]] = tag
    return indexed


def judge_coverage(model, prompt: str, key: str = "coverage") -> list[dict]:
    """Run one coverage prompt and return the judge's list of entries.

    Unlike ``tag_cujs`` (keyed per CUJ id), coverage prompts return a flat list
    under ``key``. Returns ``[]`` on any parse failure so a malformed response
    yields zero coverage rather than aborting the run.
    """
    try:
        data = extract_json(generate_json(model, prompt))
    except ValueError as e:
        logging.warning("dataset_quality: could not parse judge response: %s", e)
        return []
    items = data.get(key)
    if not isinstance(items, list):
        return []
    return [item for item in items if isinstance(item, dict)]
