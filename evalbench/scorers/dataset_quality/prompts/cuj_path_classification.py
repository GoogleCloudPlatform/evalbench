# Canonical CUJ path labels. The judge MUST return one of these exact strings;
# downstream scorers key off the named constants so they can't desync.
PATH_HAPPY = "Happy"
PATH_AMBIGUITY = "Ambiguity & Clarification"
PATH_ITERATIVE_REFINEMENT = "Iterative Refinement"
PATH_ERROR_RECOVERY = "Error Recovery"
PATH_OUT_OF_DOMAIN = "Out-of-Domain"
CUJ_PATHS = (
    PATH_HAPPY,
    PATH_AMBIGUITY,
    PATH_ITERATIVE_REFINEMENT,
    PATH_ERROR_RECOVERY,
    PATH_OUT_OF_DOMAIN,
)


CUJ_PATH_CLASSIFICATION_PROMPT = """\
You are an expert evaluator of conversational AI evaluation datasets. You are given
an ENTIRE dataset of Critical User Journeys (CUJs): each CUJ is one user-agent test
scenario, which may be single or multi-turn. Classify EVERY CUJ into EXACTLY ONE of
the five CUJ paths defined below.

Why this matters: most datasets only test the "Happy Path" -- a perfectly phrased
question that yields a perfect answer -- which gives false confidence. A healthy
dataset also exercises "unhappy paths" and edge cases (ambiguity, refinement,
error recovery, guardrails). Your classification drives a dataset-diversity score,
so judge what each scenario ACTUALLY exercises, not what it superficially resembles.

### CUJ PATHS (choose exactly one per CUJ)
- "Happy": Standard query. A direct, well-formed request the agent can satisfy in one
  straightforward pass, with no ambiguity, refinement, error, or guardrail dynamic
  along the way. Necessary as a baseline. Example: "Who are our top customers?" ->
  [the requested list].
- "Ambiguity & Clarification": The user's initial request is incomplete or
  underspecified, and the agent must ask for specific details before it can
  proceed. Example: user says "I need a database." and the agent must ask what kind
  / what name; or "top customers?" -> "by revenue or by purchase volume?".
- "Iterative Refinement": The user progressively narrows or adjusts scope across
  turns, building on the agent's previous outputs. Example: "Show Q1 sales" ->
  "actually, exclude enterprise accounts" -> "now filter to EMEA".
- "Error Recovery": The interaction centers on pivoting after something goes wrong
  -- a query/tool fails, returns an error, hits a nonexistent resource, or the user
  identifies a mistake and asks the agent to correct course. Example: "That query
  failed with a syntax error, can you fix the join?".
- "Out-of-Domain": The user requests something the product is not meant to serve or is
  disallowed from doing, and the expected behavior is to decline (and redirect where
  appropriate). What counts as out-of-domain is relative to the product's scope -- e.g.
  unsupported actions, or sensitive data it isn't meant to expose (the same data may be
  fully in-domain for a product built to serve it). The request is out-of-scope; the
  point is the refusal, not fixing anything.

### How to classify
For each CUJ, base your judgment primarily on the conversation_plan (it describes
the intended multi-turn dynamic), then the starting_prompt and expected_trajectory.
Pick the ONE path that best characterizes the scenario. If more than one seems to
apply, choose the dominant path using this priority order (top wins):
1. "Out-of-Domain" -- if the core request targets PII / disallowed / out-of-scope
   data and the point of the scenario is a refusal or redirect.
2. "Error Recovery" -- if the scenario is built around recovering from a failure,
   error, or a user-identified mistake.
3. "Iterative Refinement" -- if the user successively refines scope across turns
   based on prior results.
4. "Ambiguity & Clarification" -- if the request starts underspecified and the
   agent must seek details before acting.
5. "Happy" -- only when it is a direct, well-formed request with no ambiguity,
   refinement, error, or guardrail element.

Out-of-Domain vs Error Recovery: choose "Out-of-Domain" when the user's request is
itself out-of-scope or disallowed and the agent should refuse (e.g. asking for other
users' SSNs, or for a table the product never exposes). Choose "Error Recovery" when
the request is legitimate but execution fails or the user corrects a mistake and the
scenario is about recovering (e.g. a valid query references a column that turns out
not to exist, then the user asks to fix it).

Do not invent behavior that is not implied by the scenario. When genuinely
uncertain between an edge path and Happy, prefer the edge path only if the
conversation_plan clearly describes that dynamic; otherwise classify as "Happy".

### Input Data
**Available Tools:** {tool_names}

**CUJs (JSON list):** Each object has "id", "starting_prompt", "conversation_plan",
and "expected_trajectory".
{cujs_json}

### Recommendations
For every path whose id list you leave EMPTY, write one example starting_prompt: the
single sentence a user would open with in a CUJ that takes that path, aimed at a real
tool from the available tools. Recommend nothing for a path that already has ids.

Write it the way that user would really speak, and let the path follow from what the
request itself does -- an ask too underspecified to act on for "Ambiguity &
Clarification", a first step the user will narrow later for "Iterative Refinement", a
request this product is not meant to serve for "Out-of-Domain". The prompt must NOT
narrate the dynamic or script the agent's reply ("then ask me which region") -- an
agent handed the script is no longer being tested on recognizing the situation. Copy
the shape of this existing CUJ:
"list all instances in project gcp-project-name"

### Output Format
Return ONLY a JSON object (no markdown, no prose) with exactly this shape -- one key
per CUJ path, listing the ids of the CUJs on that path, plus "recommendations":
{{
  "Happy": ["<CUJ id, copied verbatim from the input>", "..."],
  "Ambiguity & Clarification": [],
  "Iterative Refinement": [],
  "Error Recovery": [],
  "Out-of-Domain": [],
  "recommendations": ["<one sentence a user would open with>", "..."]
}}
Include all five path keys, using an empty list for a path no CUJ takes. EVERY input
CUJ id MUST appear in exactly one path list, matching an input id exactly. Include
"recommendations" always, using an empty list when every path is already taken."""


RECOMMENDATIONS_KEY = "recommendations"


CUJ_PATH_CLASSIFICATION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        **{
            path: {"type": "ARRAY", "items": {"type": "STRING"}}
            for path in CUJ_PATHS
        },
        RECOMMENDATIONS_KEY: {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [*CUJ_PATHS, RECOMMENDATIONS_KEY],
}
