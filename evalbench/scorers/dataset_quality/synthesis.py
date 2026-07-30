"""Synthesis pass: turn the deterministic report into LLM recommendations.

Runs once, after all sub-scorers have graded the dataset. Feeds only the
already-computed report to the model -- never the raw dataset -- so the model
reasons over final signals instead of re-judging CUJs.
"""

import json
import logging

from scorers.dataset_quality.llm import generate_json
from scorers.util import extract_json
from scorers.dataset_quality.prompts.synthesis import (
    SYNTHESIS_PROMPT,
    SYNTHESIS_SCHEMA,
)


def synthesize(model, report: dict) -> None:
    """Enrich ``report`` in place with the LLM synthesis pass.

    Adds ``overall_summary`` at the top level and merges each category's
    ``assessment`` + ``recommendations`` into the matching entry in
    ``report["categories"]``. Leaves the report untouched on a parse failure,
    so a malformed response degrades to the deterministic report.
    """
    prompt = SYNTHESIS_PROMPT.format(report_json=json.dumps(report, default=str))
    raw = generate_json(model, prompt, SYNTHESIS_SCHEMA)
    try:
        synth = extract_json(raw)
    except ValueError as e:
        logging.warning("dataset_quality: synthesis parse failed: %s", e)
        return

    report["overall_summary"] = synth.get("overall_summary")

    analysis = {
        entry.get("category"): entry
        for entry in (synth.get("category_analysis") or [])
        if isinstance(entry, dict)
    }
    for category in report.get("categories") or []:
        entry = analysis.get(category.get("name"))
        if entry:
            category["assessment"] = entry.get("assessment")
            category["recommendations"] = entry.get("recommendations") or []
