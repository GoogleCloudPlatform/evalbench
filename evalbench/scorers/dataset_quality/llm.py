"""Shared LLM plumbing for the dataset-quality judge scorers.

Each LLM scorer owns its own prompt and call but shares this low-level plumbing:
JSON-mode generation, defensive JSON extraction, and tag alignment by CUJ id.
Mirrors ``scorers.mcp_style_readability`` so a slightly malformed model response
degrades gracefully instead of crashing the run.
"""

import json
import logging
import re


def generate_json(model, prompt: str) -> str:
    """Generate a model response as raw JSON text.

    Prefer Gemini's native JSON mode via the underlying genai client (guarantees
    valid JSON and bypasses ``GeminiGenerator.generate``'s SQL sanitizer, which
    corrupts JSON escapes). Falls back to plain ``generate`` for other models.
    """
    client = getattr(model, "client", None)
    caller = getattr(model, "_call_generate_content", None)
    if client is not None and callable(caller):
        try:
            from google.genai import types

            config = types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0,
            )
            resp = caller(contents=prompt, config=config)
            text = getattr(resp, "text", None)
            if text:
                return text
        except Exception as e:
            logging.warning(
                "dataset_quality: JSON-mode generation failed (%s); "
                "falling back to plain generate().",
                e,
            )
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
