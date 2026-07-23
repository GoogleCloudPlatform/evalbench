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


def synthesize(model, report: dict) -> None:
    """Enrich ``report`` in place with the LLM synthesis pass.

    Adds ``overall_summary`` and ``prioritized_actions`` at the top level and
    merges each category's ``assessment`` + ``recommendations`` into the matching
    entry in ``report["categories"]`` (by name), so the LLM's prose lands next to
    the deterministic score it describes. Leaves the report untouched on any parse
    failure, so a malformed response degrades to the deterministic report.
    """
    prompt = SYNTHESIS_PROMPT.format(report_json=json.dumps(report, default=str))
    raw = generate_json(model, prompt, SYNTHESIS_SCHEMA)
    try:
        synth = extract_json(raw)
    except ValueError as e:
        logging.warning("dataset_quality: synthesis parse failed: %s", e)
        return

    report["overall_summary"] = synth.get("overall_summary")
    report["prioritized_actions"] = synth.get("prioritized_actions") or []

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
