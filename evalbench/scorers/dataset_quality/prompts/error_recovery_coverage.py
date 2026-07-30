# Canonical failure/recovery modes, named for how the call fails rather than for
# any one product's domain, so the taxonomy applies to any tool-calling product.
# Deliberately coarse: evalbench injects no faults, so a mode belongs here only if
# a CUJ can provoke it from a live server using prompt text and env alone, and only
# if recovering from it demands something distinct of the agent.
# The judge MUST return only these exact strings; the scorer keys coverage off the
# named constants so they can't desync.
MODE_INVALID_REQUEST = "Invalid Request"
MODE_PERMISSION_DENIED = "Permission Denied"
MODE_INCOMPLETE_RESULT = "Incomplete Result"
ERROR_RECOVERY_MODES = (
    MODE_INVALID_REQUEST,
    MODE_PERMISSION_DENIED,
    MODE_INCOMPLETE_RESULT,
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
- "Invalid Request": the call is rejected because of what it names or passes -- a
  resource that does not exist (bad id, wrong name, wrong project or region), or an
  argument that is missing, malformed, or out of range. The agent must recognize the
  bad request and fix it (correct the reference, look the right value up, ask the
  user) rather than reissuing it unchanged.
- "Permission Denied": the call is rejected for permission or auth reasons --
  forbidden resource, missing scope, expired or invalid credentials. Distinct from
  "Invalid Request": the request is well formed and the target exists, but this
  caller is not allowed to touch it, so reissuing it cannot help and the agent must
  surface the denial or route around it.
- "Incomplete Result": the call succeeds but does not return everything the task
  needs -- an empty set, no match, a truncated or paginated page, or missing fields.
  The agent must notice the gap (page through, broaden the query, report that
  nothing was found) instead of treating what came back as the whole answer.

A single CUJ may exercise several modes; list every mode it genuinely exercises.
When genuinely borderline on whether a mode is exercised, do NOT list it.

### Input Data
**Available Tools:** {tool_names}

**Tool Catalog (JSON):** each tool's name and description. Read the descriptions as
the evidence for which failures a tool can actually produce -- documented limits,
pagination, required roles, and resource lookups tell you which modes are reachable
on that tool.
{tool_catalog}

**CUJs (JSON list):** Each object has "id", "starting_prompt", "conversation_plan",
and "expected_trajectory".
{cujs_json}

### Recommendations
For every mode whose id list you leave EMPTY, write one example starting_prompt: the
single sentence a user would type to walk the agent into that failure, aimed at a real
tool from the catalog. Recommend nothing for a mode that already has ids.

Write it the way that user would really speak. They do not know the call is about to
fail, so the prompt must NOT narrate the failure, cite the IAM permission they lack,
or mention the limit the result will exceed -- an agent handed that explanation is no
longer being tested on whether it notices. Ask for the thing plainly and let the
failure come from what the request names. Copy the shape of this existing CUJ:
"Update the instance 'non-existent-db-123' to have 8 cores."

### Output Format
Return ONLY a JSON object (no markdown, no prose) with exactly this shape -- one key
per mode listing the ids of the CUJs that exercise it, plus "recommendations":
{{
  "Invalid Request": ["<CUJ id, copied verbatim from the input>", "..."],
  "Permission Denied": [],
  "Incomplete Result": [],
  "recommendations": ["<one sentence a user would type to provoke it>", "..."]
}}
Include all three mode keys, using an empty list for a mode no CUJ exercises. Every id
MUST match an input CUJ id exactly. List a CUJ under every mode it exercises; a CUJ
that exercises none appears in no list. Include "recommendations" always, using an
empty list when every mode is already covered."""


RECOMMENDATIONS_KEY = "recommendations"


ERROR_RECOVERY_COVERAGE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        **{
            mode: {"type": "ARRAY", "items": {"type": "STRING"}}
            for mode in ERROR_RECOVERY_MODES
        },
        RECOMMENDATIONS_KEY: {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [*ERROR_RECOVERY_MODES, RECOMMENDATIONS_KEY],
}
