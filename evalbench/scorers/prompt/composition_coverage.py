COMPOSITION_COVERAGE_PROMPT = """\
You are an expert evaluator of conversational AI evaluation datasets. You are given
an ENTIRE dataset of Critical User Journeys (CUJs): each CUJ is one user-agent test
scenario, which may be single- or multi-turn. For EVERY CUJ, make two independent
judgments about how it exercises TOOL COMPOSITION.

Why this matters: real tasks rarely resolve with a single isolated tool call. Strong
datasets test whether the agent can chain tools together, pass data across
skills/tools, and respect ordering constraints. A dataset of one-tool-per-scenario
requests overstates how well the product handles realistic, composite work. Your
judgments drive a composition-coverage score.

### JUDGMENT 1 -- is_multi_tool
Does the CUJ genuinely require MORE THAN ONE distinct tool (or a skill-plus-tool, or
cross-skill data passing) working together to succeed?
- Judge from what the starting_prompt and conversation_plan actually demand, using
  expected_trajectory as a strong hint -- NOT from the trajectory list alone. A
  trajectory may list several tools that the task does not truly require, or the same
  tool repeated; treat repeats of one tool as single-tool.
- true: the task's success depends on two or more DIFFERENT tools/skills
  contributing (e.g. read a file, then run a test; search, then summarize; produce
  data in one skill and consume it in another).
- false: the task is satisfiable with a single tool/skill (even if called more than
  once).

### JUDGMENT 2 -- has_sequence_dependency
Does success require a SPECIFIC ORDER of operations -- tool A must happen before
tool B, and doing them out of order would fail or produce a wrong result?
- true: there is a real ordering constraint (e.g. create the table before inserting
  rows; check out the branch before editing; fetch an id before using it).
- false: the tools are independent / order does not matter, or there is only one
  operation.
- Note: needing multiple tools (Judgment 1 = true) does NOT by itself imply an
  ordering constraint. Judge ordering separately.

When genuinely borderline on either judgment, answer false.

### Input Data
**Available Tools:** {tool_names}

**CUJs (JSON list):** Each object has "id", "starting_prompt", "conversation_plan",
and "expected_trajectory".
{cujs_json}

### OUTPUT
Return ONLY a JSON object (no markdown, no prose) with exactly this shape:
{{
  "tags": [
    {{
      "id": "<the CUJ id, copied verbatim from the input>",
      "is_multi_tool": true|false,
      "has_sequence_dependency": true|false
    }}
  ]
}}
Return one entry in "tags" for EVERY CUJ in the input. Each "id" MUST match an input
CUJ id exactly."""
