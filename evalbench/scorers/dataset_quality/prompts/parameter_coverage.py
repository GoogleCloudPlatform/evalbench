PARAMETER_COVERAGE_PROMPT = """\
You are an expert evaluator of conversational AI evaluation datasets. You are given
the TOOL SCHEMA for every tool this product's dataset actually calls, and an ENTIRE
dataset of Critical User Journeys (CUJs): each CUJ is one user-agent test scenario,
which may be single- or multi-turn. For EVERY named parameter of EVERY tool in the
schema, decide which CUJs EXERCISE that parameter.

Why this matters: a tool's parameters define its behavior surface. If a dataset only
ever hits a tool's required identifiers and never its optional filters, enums, or
flags, those parameters are effectively untested -- the product could regress on them
silently. A named parameter that appears in zero (or very few) CUJs is a coverage
gap. Your judgments drive a parameter-coverage score, so ground every decision in
what each scenario actually demands.

### What "exercises a parameter" means
A CUJ exercises a tool's parameter when the scenario would require the agent to
supply a meaningful, scenario-specific value for that parameter while calling that
tool -- i.e. the request cannot succeed as described unless that parameter is set.
- Count a parameter for a CUJ only if the CUJ actually calls that parameter's tool
  -- use expected_trajectory as the ground truth for which tools run, since whether a
  tool fires is a fact to read off the trajectory, not something to infer from the
  prompt. A parameter whose tool never appears in the trajectory is NOT exercised.
- A required parameter is exercised whenever its tool is invoked (the call cannot be
  made without it). An optional parameter is exercised only when the scenario
  explicitly demands its behavior -- e.g. a filter, page size, enum choice, ordering,
  or flag that the starting_prompt or conversation_plan calls for.
- Judge from what the starting_prompt and conversation_plan actually demand, using
  expected_trajectory to confirm which tools run. Do NOT assume an optional parameter
  is used just because it exists; absence of any demand for it means it is NOT
  exercised by that CUJ.
- The same CUJ may exercise several parameters (across one or more tools). List the
  CUJ under every parameter it genuinely exercises.

When genuinely borderline on whether an optional parameter is exercised, do NOT count
it.

### Input Data
**Tool Schema (JSON):** The tools at least one CUJ's expected_trajectory names. Each
tool has "name" and an "inputSchema" whose "properties" are the named parameters and
whose "required" lists the mandatory ones.
{tool_schema}

**CUJs (JSON list):** Each object has "id", "starting_prompt", "conversation_plan",
and "expected_trajectory".
{cujs_json}

### Recommendations
For every TOOL with one or more parameters whose "cuj_ids" you leave EMPTY, write one
example starting_prompt: the single sentence a user would type to walk the agent into
using that tool's uncovered parameters. One prompt per tool, NOT one per parameter.
Recommend nothing for a tool whose parameters are all covered.

Write it the way that user would really speak -- a goal plus the one or two
constraints they actually care about, stated as an outcome they want rather than as
settings to apply. Realism beats coverage: a sentence that recites a checklist of
settings is worthless as a CUJ, so cover FEWER uncovered parameters rather than
enumerate them all. Do NOT name the parameters or the tool, and do not hand over
values the agent should have to look up or ask for -- an agent given the whole call is
no longer being tested on constructing it. Copy the shape of this existing CUJ:
"list all instances in project gcp-project-name"

### Output Format
Return ONLY a JSON object (no markdown, no prose) with exactly this shape:
{{
  "coverage": [
    {{
      "tool": "<tool name, copied verbatim from the schema>",
      "parameter": "<parameter name, copied verbatim from the schema>",
      "cuj_ids": ["<id of each CUJ that exercises this parameter>"]
    }}
  ],
  "recommendations": ["<one sentence a user would type>", "..."]
}}
Emit exactly ONE entry for EVERY named parameter of EVERY tool in the schema --
including parameters exercised by no CUJ, whose "cuj_ids" MUST be an empty list. Each
id in "cuj_ids" MUST match an input CUJ id exactly, with no duplicates. Include
"recommendations" always, using an empty list when every parameter is already
covered."""


RECOMMENDATIONS_KEY = "recommendations"


PARAMETER_COVERAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "coverage": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "tool": {"type": "STRING"},
                    "parameter": {"type": "STRING"},
                    "cuj_ids": {
                        "type": "ARRAY",
                        "items": {"type": "STRING"},
                    },
                },
                "required": ["tool", "parameter", "cuj_ids"],
            },
        },
        RECOMMENDATIONS_KEY: {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["coverage", RECOMMENDATIONS_KEY],
}
