# Canonical failure/recovery modes. The judge MUST return only these exact
# strings; the scorer keys coverage off the named constants so they can't desync.
MODE_ACCESS_DENIED = "Access Denied"
MODE_LATENCY_THROTTLING = "Latency / Throttling"
MODE_OUTPUT_VALIDATION = "Output Validation Failure"
MODE_NULL_RESULTS = "Null Results"
MODE_INCOMPLETE_DATA = "Incomplete Data Set"
MODE_CASCADING_FAILURE = "Cascading Failure"
ERROR_RECOVERY_MODES = (
    MODE_ACCESS_DENIED,
    MODE_LATENCY_THROTTLING,
    MODE_OUTPUT_VALIDATION,
    MODE_NULL_RESULTS,
    MODE_INCOMPLETE_DATA,
    MODE_CASCADING_FAILURE,
)


ERROR_RECOVERY_COVERAGE_PROMPT = """\
You are an expert evaluator of conversational AI evaluation datasets. You are given
an ENTIRE dataset of Critical User Journeys (CUJs): each CUJ is one user-agent test
scenario, which may be single or multi-turn. For EVERY CUJ, decide which ERROR /
RECOVERY modes it genuinely exercises -- zero, one, or several.

Why this matters: production tools fail in many distinct ways, and a robust agent
must handle each. A dataset that only tests the Happy Path -- or tests just one
failure mode -- gives false confidence that the agent recovers from the rest. This
score measures how many of the failure/recovery modes below the dataset covers, so
judge what each scenario ACTUALLY exercises, not what it superficially resembles.

### ERROR / RECOVERY MODES
A CUJ exercises a mode when its scenario genuinely puts the agent in that failure
situation and success depends on the agent detecting or recovering from it -- judged
from the starting_prompt, conversation_plan, and expected_trajectory, NOT from
surface wording.
- "Access Denied": a tool call is rejected for permission/auth reasons (forbidden
  resource, missing scope, expired credentials) and the agent must surface or work
  around the denial.
- "Latency / Throttling": a call is slow, times out, or is rate-limited/throttled,
  and the agent must retry, back off, or adapt.
- "Output Validation Failure": a tool returns malformed, schema-invalid, or
  otherwise unusable output that the agent must detect and handle rather than trust.
- "Null Results": a call succeeds but returns nothing (empty set, no match), and the
  agent must handle the empty case instead of assuming data exists.
- "Incomplete Data Set": a call returns partial or truncated data (missing fields,
  capped page, partial coverage) and the agent must recognize and account for it.
- "Cascading Failure": one failure triggers or compounds another across steps, and
  the agent must recover without making the situation worse.

A single CUJ may exercise several modes; list every mode it genuinely exercises.
When genuinely borderline on whether a mode is exercised, do NOT list it.

### Input Data
**Available Tools:** {tool_names}

**CUJs (JSON list):** Each object has "id", "starting_prompt", "conversation_plan",
and "expected_trajectory".
{cujs_json}

### Output Format
Return ONLY a JSON object (no markdown, no prose) with exactly this shape:
{{
  "tags": [
    {{
      "id": "<the CUJ id, copied verbatim from the input>",
      "modes": ["<zero or more of the exact mode strings above>"]
    }}
  ]
}}
Return one entry in "tags" for EVERY CUJ in the input. Each "id" MUST match an input
CUJ id exactly. Each string in "modes" MUST be exactly one of the mode names listed
above; use an empty list when the CUJ exercises none."""


ERROR_RECOVERY_COVERAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "tags": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "id": {"type": "STRING"},
                    "modes": {
                        "type": "ARRAY",
                        "items": {
                            "type": "STRING",
                            "enum": list(ERROR_RECOVERY_MODES),
                        },
                    },
                },
                "required": ["id", "modes"],
            },
        },
    },
    "required": ["tags"],
}
