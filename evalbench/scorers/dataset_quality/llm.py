"""Shared LLM plumbing for the dataset-quality judge scorers.

Each LLM scorer owns its own prompt and call but shares this low-level plumbing:
JSON-mode generation, defensive JSON extraction, and tag alignment by CUJ id.
Mirrors ``scorers.mcp_style_readability`` so a slightly malformed model response
degrades gracefully instead of crashing the run.
"""

import json
import logging
import re


# Max output for the target Gemini Pro models. Gemini 3.x draws reasoning tokens
# from the output budget, so a high cap keeps thought tokens from starving the
# JSON answer (which otherwise finishes with empty ``.text``).
_MAX_OUTPUT_TOKENS = 65536


def _finish_reason(resp) -> str:
    try:
        return str(resp.candidates[0].finish_reason)
    except (AttributeError, IndexError, TypeError):
        return "unknown"


def generate_json(model, prompt: str, response_schema: dict | None = None) -> str:
    """Generate a model response as raw JSON text.

    Prefer Gemini's native JSON mode via the underlying genai client: it bypasses
    ``GeminiGenerator.generate``'s SQL sanitizer, which corrupts JSON escapes. An
    empty/failed Gemini call returns "" rather than falling back to
    ``model.generate`` -- that path re-runs the sanitizer on the failed response.
    Only non-Gemini models (no native JSON mode) use ``model.generate``.

    ``response_schema`` (a plain dict) constrains Gemini via constrained decoding
    -- the reliable fix for Gemini 3.x JSON mode, which otherwise drops/adds
    braces or cuts values mid-token. Ignored on the non-Gemini fallback path.
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
                response_schema=response_schema,
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
        pass
    # Fall back to the outermost {...} span for prose-wrapped responses.
    start, end = text.find("{"), text.rfind("}")
    if start != -1 and end > start:
        try:
            return json.loads(text[start:end + 1])
        except json.JSONDecodeError:
            pass
    raise ValueError("no JSON object found in model response")


def _log_parse_failure(raw: str, err: Exception) -> None:
    snippet = (raw or "").strip().replace("\n", " ")
    if len(snippet) > 500:
        snippet = snippet[:500] + "...[truncated]"
    logging.warning(
        "dataset_quality: could not parse judge response: %s | len=%d raw=%r",
        err,
        len(raw or ""),
        snippet,
    )


def tag_cujs(
    model, prompt: str, response_schema: dict | None = None
) -> dict[str, dict] | None:
    """Run one tagging prompt and return the per-CUJ tags indexed by id.

    Returns ``None`` when the judge call itself failed (empty/unparseable
    response, or a response missing the ``tags`` list) so the caller can drop
    the metric as inapplicable instead of scoring a confident 0. A successfully
    parsed response returns the indexed tags (possibly empty).
    """
    raw = generate_json(model, prompt, response_schema)
    try:
        data = extract_json(raw)
    except ValueError as e:
        _log_parse_failure(raw, e)
        return None
    tags = data.get("tags")
    if not isinstance(tags, list):
        _log_parse_failure(raw, ValueError("response missing 'tags' list"))
        return None
    indexed = {}
    for tag in tags:
        if isinstance(tag, dict) and tag.get("id") is not None:
            indexed[tag["id"]] = tag
    return indexed


def judge_coverage(
    model, prompt: str, response_schema: dict | None = None
) -> list[dict] | None:
    """Run one coverage prompt and return the judge's list of entries.

    Unlike ``tag_cujs`` (keyed per CUJ id), coverage prompts return a flat list
    under ``coverage``. Returns ``None`` when the judge call itself failed
    (empty/unparseable response, or a response missing the ``coverage`` list) so
    the caller can drop the metric as inapplicable instead of scoring a confident
    0. A successfully parsed response returns the list (possibly empty).
    """
    raw = generate_json(model, prompt, response_schema)
    try:
        data = extract_json(raw)
    except ValueError as e:
        _log_parse_failure(raw, e)
        return None
    items = data.get("coverage")
    if not isinstance(items, list):
        _log_parse_failure(raw, ValueError("response missing 'coverage' list"))
        return None
    return [item for item in items if isinstance(item, dict)]
