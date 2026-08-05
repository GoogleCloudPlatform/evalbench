"""Shared LLM plumbing for the dataset-quality judge scorers: JSON-mode
generation and CUJ id grouping by label."""

import logging

from scorers.util import extract_json


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


def generate_json(model, prompt: str, response_schema: dict | None = None) -> str:
    """Generate a model response as raw JSON text.

    Uses Gemini's native JSON mode via the underlying genai client, which bypasses
    ``GeminiGenerator.generate``'s SQL sanitizer (it corrupts JSON escapes). Only
    non-Gemini models fall back to ``model.generate``.
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
                http_options=types.HttpOptions(timeout=_REQUEST_TIMEOUT_MS),
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
            "dataset_quality: JSON-mode returned empty text (finish_reason=%s)",
            _finish_reason(resp),
        )
        return ""
    return model.generate(prompt)


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


def judge_labeled_json(
    model, prompt: str, response_schema: dict, labels
) -> dict | None:
    """Run one tagging prompt and return the whole parsed response.

    Use over :func:`group_cuj_ids` when the prompt returns keys beyond the label
    lists. Returns ``None`` when the judge call itself failed (empty/unparseable
    response, or not a single label list) so the caller can drop the metric as
    inapplicable instead of scoring a confident 0.
    """
    raw = generate_json(model, prompt, response_schema)
    try:
        data = extract_json(raw)
    except ValueError as e:
        _log_parse_failure(raw, e)
        return None
    if not any(isinstance(data.get(label), list) for label in labels):
        _log_parse_failure(raw, ValueError("response has no label id lists"))
        return None
    return data


def group_ids(data: dict, labels, dataset_ids: list[str]) -> dict[str, list[str]]:
    """Pull ``{label: [cuj_id]}`` out of an already-parsed judge response.

    Ids absent from ``dataset_ids`` and repeats within a label are dropped; each
    list follows dataset order.
    """
    order = {cuj_id: i for i, cuj_id in enumerate(dataset_ids)}
    grouped = {}
    for label in labels:
        ids = data.get(label)
        ids = ids if isinstance(ids, list) else []
        grouped[label] = sorted(
            {i for i in ids if i in order}, key=order.__getitem__
        )
    return grouped


def group_cuj_ids(
    model,
    prompt: str,
    response_schema: dict,
    labels,
    dataset_ids: list[str],
) -> dict[str, list[str]] | None:
    """Run one tagging prompt and return ``{label: [cuj_id]}`` for ``labels``."""
    data = judge_labeled_json(model, prompt, response_schema, labels)
    if data is None:
        return None
    return group_ids(data, labels, dataset_ids)


def judge_coverage(
    model, prompt: str, response_schema: dict | None = None
) -> dict | None:
    """Run one coverage prompt and return the whole parsed response.

    ``coverage`` is normalized to a list of dicts; keys beyond it (e.g.
    recommendations) are passed through untouched. Returns ``None`` when the
    judge call itself failed (empty/unparseable response, or a response missing
    the ``coverage`` list) so the caller can drop the metric as inapplicable
    instead of scoring a confident 0.
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
    data["coverage"] = [item for item in items if isinstance(item, dict)]
    return data


def example_prompts(data: dict, key: str) -> list[str]:
    """The judge's example prompts under ``key``, deduped.

    Whitespace is collapsed because the report renders one suggestion per line.
    """
    items = data.get(key)
    if not isinstance(items, list):
        return []
    examples = []
    for item in items:
        example = " ".join(item.split()) if isinstance(item, str) else ""
        if example and example not in examples:
            examples.append(example)
    return examples
