"""Synthesis pass: turn the deterministic report into LLM recommendations.

Runs once, after all sub-scorers have graded the dataset. Feeds only the
already-computed report (scores, counts, suggestions, per-CUJ evidence) to the
model -- never the raw dataset -- so the model reasons over final signals instead
of re-judging CUJs. Returns ``{}`` on any parse failure so a malformed response
leaves the deterministic report intact rather than aborting the run.
"""

import json
import logging

from scorers.dataset_quality.llm import extract_json, generate_json
from scorers.dataset_quality.prompts.synthesis import (
    SYNTHESIS_PROMPT,
    SYNTHESIS_SCHEMA,
)


def synthesize(model, report: dict) -> dict:
    prompt = SYNTHESIS_PROMPT.format(report_json=json.dumps(report, default=str))
    raw = generate_json(model, prompt, SYNTHESIS_SCHEMA)
    try:
        return extract_json(raw)
    except ValueError as e:
        logging.warning("dataset_quality: synthesis parse failed: %s", e)
        return {}
