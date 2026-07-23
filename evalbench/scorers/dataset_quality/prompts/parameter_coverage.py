PARAMETER_COVERAGE_PROMPT = """\
You are an expert evaluator of conversational AI evaluation datasets. You are given
the full TOOL SCHEMA for a product and an ENTIRE dataset of Critical User Journeys
(CUJs): each CUJ is one user-agent test scenario, which may be single- or
multi-turn. For EVERY named parameter of EVERY tool in the schema, decide which CUJs
EXERCISE that parameter.

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
- Count a parameter for a CUJ only if that parameter's tool is actually used by the
  CUJ. Use expected_trajectory as the authoritative signal for which tools a CUJ
  invokes; a parameter belonging to a tool the CUJ never calls is NOT exercised.
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
**Tool Schema (JSON):** A list of tools. Each tool has "name" and an "inputSchema"
whose "properties" are the named parameters and whose "required" lists the mandatory
ones.
{tool_schema}

**CUJs (JSON list):** Each object has "id", "starting_prompt", "conversation_plan",
and "expected_trajectory".
{cujs_json}

### Output Format
Return ONLY a JSON object (no markdown, no prose) with exactly this shape:
{{
  "coverage": [
    {{
      "tool": "<tool name, copied verbatim from the schema>",
      "parameter": "<parameter name, copied verbatim from the schema>",
      "cuj_ids": ["<id of each CUJ that exercises this parameter>"]
    }}
  ]
}}
Emit exactly ONE entry for EVERY named parameter of EVERY tool in the schema --
including parameters exercised by no CUJ, whose "cuj_ids" MUST be an empty list. Each
id in "cuj_ids" MUST match an input CUJ id exactly, with no duplicates."""


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
    },
    "required": ["coverage"],
}
