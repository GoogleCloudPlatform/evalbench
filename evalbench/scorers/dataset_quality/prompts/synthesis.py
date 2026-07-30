SYNTHESIS_PROMPT = """\
You are an expert reviewer of agent evaluation datasets. A dataset is a set of
Critical User Journeys (CUJs) used to test how well an agent drives a product's
tools. A separate, deterministic scoring pass has already graded one product's
dataset and produced the report below. Your job is to turn that report into a
clear, prioritized analysis the dataset author can act on.

### The graded report (your ONLY source of truth)
{report_json}

### How to read the report
- ``total_cujs``: the dataset size; the denominator for every count and share,
  so use it to judge whether a raw count is a big or small gap.
- ``dataset_quality_score`` / ``letter_grade``: the overall grade.
- ``categories``: one entry per top-level grade area, each with:
  - ``score``: 0-100 for the category (lower means a bigger gap).
  - ``sub_scores``: 0-100 per individual scorer that rolls into it; a low
    category is best explained by its weakest sub_score.
  - ``metrics``: raw counts behind those scores (tools/params covered, etc.).
  - ``gaps``: precomputed, factual gap statements for this category.
  - ``example_prompts``: starting prompts illustrating CUJs this category is
    missing. They reach the author only through you, so give each one its own
    recommendation: say what to add and what it covers, then introduce the prompt
    with "for example:" and quote it verbatim. Name at most two or
    three representative tools or parameters it covers; never restate a list
    ``gaps`` already spells out in full.
  - ``evidence``: per-CUJ classifications (which CUJ ids were tagged vague,
    which are multi-tool, etc.).
- ``cuj_path_distribution``: dataset-wide count of CUJs per interaction path
  (Happy, Ambiguity & Clarification, Iterative Refinement, Error Recovery,
  Out-of-Domain). Most datasets skew to Happy; a thin tail on the unhappy paths
  is a diversity gap.

### Rules (critical for accuracy)
- Reason ONLY from the numbers, counts, gaps, and evidence in the report.
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
      "category": "<the ``name`` of an entry in ``categories``>",
      "assessment": "<1-2 sentences on what this score means for the dataset>",
      "recommendations": ["<concrete, actionable step>", "..."]
    }}
  ]
}}
Include one ``category_analysis`` entry per entry in ``categories`` (use its
``name`` as the ``category``); in each ``assessment``, name the weakest
``sub_score`` driving that category. Order each category's
``recommendations`` lowest-scoring gaps first (rank by score; no weights are
provided)."""


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
                    "assessment": {"type": "STRING"},
                    "recommendations": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
                "required": [
                    "category",
                    "assessment",
                    "recommendations",
                ],
            },
        },
    },
    "required": [
        "overall_summary",
        "category_analysis",
    ],
}
