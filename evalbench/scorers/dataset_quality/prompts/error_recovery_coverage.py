# Canonical failure/recovery modes, named for how the call fails rather than for
# any one product's domain, so the taxonomy applies to any tool-calling product.
# The judge MUST return only these exact strings; the scorer keys coverage off the
# named constants so they can't desync.
MODE_ACCESS_DENIED = "Access Denied"
MODE_TRANSIENT_FAILURE = "Transient Failure"
MODE_MALFORMED_OUTPUT = "Malformed Output"
MODE_EMPTY_RESULT = "Empty Result"
MODE_PARTIAL_RESULT = "Partial Result"
MODE_CASCADING_FAILURE = "Cascading Failure"
ERROR_RECOVERY_MODES = (
    MODE_ACCESS_DENIED,
    MODE_TRANSIENT_FAILURE,
    MODE_MALFORMED_OUTPUT,
    MODE_EMPTY_RESULT,
    MODE_PARTIAL_RESULT,
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
- "Access Denied": a call is rejected for permission/auth reasons (forbidden
  resource, missing scope, expired or invalid credentials) and the agent must
  surface or work around the denial.
- "Transient Failure": a call fails in a way that could succeed on a retry -- it is
  slow, times out, is rate-limited/throttled, or the backend is temporarily
  unavailable -- and the agent must retry, back off, or adapt.
- "Malformed Output": a call returns output the agent cannot take at face value --
  unparseable, schema-invalid, or otherwise unusable -- and the agent must detect
  that rather than act on it.
- "Empty Result": a call succeeds but returns nothing (no match, empty set), and the
  agent must handle the empty case instead of assuming a result exists.
- "Partial Result": a call returns only part of what was asked for (truncated,
  paginated, missing fields, partial coverage) and the agent must recognize and
  account for the gap.
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
Return ONLY a JSON object (no markdown, no prose) with exactly this shape -- one key
per mode, listing the ids of the CUJs that exercise it:
{{
  "Access Denied": ["<CUJ id, copied verbatim from the input>", "..."],
  "Transient Failure": [],
  "Malformed Output": [],
  "Empty Result": [],
  "Partial Result": [],
  "Cascading Failure": []
}}
Include all six keys, using an empty list for a mode no CUJ exercises. Every id MUST
match an input CUJ id exactly. List a CUJ under every mode it exercises; a CUJ that
exercises none appears in no list."""


ERROR_RECOVERY_COVERAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        mode: {"type": "ARRAY", "items": {"type": "STRING"}}
        for mode in ERROR_RECOVERY_MODES
    },
    "required": list(ERROR_RECOVERY_MODES),
}
