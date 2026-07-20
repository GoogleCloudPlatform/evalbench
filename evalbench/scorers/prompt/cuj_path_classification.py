# Canonical CUJ path labels. The judge MUST return one of these exact strings;
# downstream scorers key off them, so keep them in sync with any consumer.
CUJ_PATHS = (
    "Happy",
    "Ambiguity & Clarification",
    "Iterative Refinement",
    "Error Recovery",
    "Out-of-Domain",
)


CUJ_PATH_CLASSIFICATION_PROMPT = """\
You are an expert evaluator of conversational AI evaluation datasets. You are given
an ENTIRE dataset of Critical User Journeys (CUJs): each CUJ is one user-agent test
scenario, which may be single- or multi-turn. Classify EVERY CUJ into EXACTLY ONE of
the five CUJ paths defined below.

Why this matters: most datasets only test the "Happy Path" -- a perfectly phrased
question that yields a perfect answer -- which gives false confidence. A healthy
dataset also exercises "unhappy paths" and edge cases (ambiguity, refinement,
error recovery, guardrails). Your classification drives a dataset-diversity score,
so judge what each scenario ACTUALLY exercises, not what it superficially resembles.

### CUJ PATHS (choose exactly one per CUJ)
- "Happy": Standard query. A direct, well-formed request that leads straight to an
  accurate response. Necessary as a baseline. Example: "Who are our top customers?"
  -> [accurate list].
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
- "Out-of-Domain": The user requests something the agent should refuse or redirect
  -- e.g. PII/sensitive data, disallowed actions, or data/tables that do not exist
  -- and the expected behavior is a polite refusal plus redirection (guardrail).

### How to classify
For each CUJ, base your judgment primarily on the conversation_plan (it describes
the intended multi-turn dynamic), then the starting_prompt and expected_trajectory.
Pick the ONE path that best characterizes the scenario. If more than one seems to
apply, choose the dominant path using this priority order (top wins):
1. "Out-of-Domain" -- if the core request targets PII / disallowed / nonexistent
   data and the point of the scenario is a refusal or redirect.
2. "Error Recovery" -- if the scenario is built around recovering from a failure,
   error, or a user-identified mistake.
3. "Iterative Refinement" -- if the user successively refines scope across turns
   based on prior results.
4. "Ambiguity & Clarification" -- if the request starts underspecified and the
   agent must seek details before acting.
5. "Happy" -- only when it is a direct, well-formed request with no ambiguity,
   refinement, error, or guardrail element.

Do not invent behavior that is not implied by the scenario. When genuinely
uncertain between an edge path and Happy, prefer the edge path only if the
conversation_plan clearly describes that dynamic; otherwise classify as "Happy".

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
      "cuj_path": "Happy | Ambiguity & Clarification | Iterative Refinement | Error Recovery | Out-of-Domain"
    }}
  ]
}}
Return one entry in "tags" for EVERY CUJ in the input. Each "id" MUST match an input
CUJ id exactly. Each "cuj_path" value MUST be exactly one of the five strings listed
above."""
