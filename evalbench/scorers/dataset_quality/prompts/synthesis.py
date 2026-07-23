SYNTHESIS_PROMPT = """\
You are an expert reviewer of agent evaluation datasets. A dataset is a set of
Critical User Journeys (CUJs) used to test how well an agent drives a product's
tools. A separate, deterministic scoring pass has already graded one product's
dataset and produced the report below. Your job is to turn that report into a
clear, prioritized analysis the dataset author can act on.

### The graded report (your ONLY source of truth)
{report_json}

### How to read the report
- ``dataset_quality_score`` / ``letter_grade``: the overall grade.
- ``category_scores`` / ``sub_scores``: 0-100 per category and per sub-metric;
  lower means a bigger gap.
- ``row_fields``: raw counts (tools/params covered, CUJ path distribution, etc.).
- ``suggestions``: precomputed, factual gap statements, each tagged with the
  ``scorer`` and ``category`` it came from.
- ``evidence``: per-CUJ classifications (which CUJ ids were tagged vague, which
  path each CUJ is on, which are multi-tool, etc.).

### Rules (critical for accuracy)
- Reason ONLY from the numbers, counts, suggestions, and evidence in the report.
  Do NOT invent tools, parameters, CUJs, or gaps that the report does not state.
- Do NOT re-judge or recompute any score; treat the numbers as final.
- Every recommendation must be traceable to a specific signal in the report
  (cite the metric, count, or evidence it comes from in the rationale).
- Be concrete and actionable: say which CUJs to add or how to change existing
  ones, and prefer the lowest-scoring / least-covered areas first.

### Output Format
Return ONLY a JSON object (no markdown, no prose) with exactly this shape:
{{
  "overall_summary": "<2-3 sentences on the dataset's overall health, its grade, and its single biggest gap>",
  "category_analysis": [
    {{
      "category": "<category name from category_scores>",
      "score": <the category's score, copied from the report>,
      "assessment": "<1-2 sentences on what this score means for the dataset>",
      "recommendations": ["<concrete, actionable step>", "..."]
    }}
  ],
  "prioritized_actions": [
    {{
      "priority": <integer, 1 = do first>,
      "area": "<the category or sub-metric this targets>",
      "action": "<what to add or change, concretely>",
      "rationale": "<the specific signal from the report that motivates this>"
    }}
  ]
}}
Include one ``category_analysis`` entry per category in ``category_scores``. Order
``prioritized_actions`` by impact, lowest-scoring gaps first."""


SYNTHESIS_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "overall_summary": {"type": "STRING"},
        "category_analysis": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "category": {"type": "STRING"},
                    "score": {"type": "NUMBER"},
                    "assessment": {"type": "STRING"},
                    "recommendations": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
                "required": [
                    "category",
                    "score",
                    "assessment",
                    "recommendations",
                ],
            },
        },
        "prioritized_actions": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "priority": {"type": "INTEGER"},
                    "area": {"type": "STRING"},
                    "action": {"type": "STRING"},
                    "rationale": {"type": "STRING"},
                },
                "required": ["priority", "area", "action", "rationale"],
            },
        },
    },
    "required": [
        "overall_summary",
        "category_analysis",
        "prioritized_actions",
    ],
}
