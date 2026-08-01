# Output key the judge groups CUJ ids under; the scorer reads the same constant
# so prompt and scorer can't desync.
KEY_VAGUE = "vague_ids"


VAGUE_EXAMPLES_PROMPT = """\
You are an expert evaluator of conversational AI evaluation datasets. You are given
an ENTIRE dataset of Critical User Journeys (CUJs): each CUJ is one user-agent test
scenario, which may be single or multi-turn. For EVERY CUJ, decide whether the
user's request is VAGUE / INDIRECT or DIRECT.

Why this matters: a healthy dataset must test discoverability -- can the agent infer
WHICH tool or operation to use when the user describes an outcome or intent instead
of naming the tool explicitly. Datasets stuffed only with direct, tool-naming
commands overstate how usable the product is, because real users rarely know the
exact tool names. Your judgments drive a discoverability score, so judge from how
the user ACTUALLY phrases the request, not from what would make a stronger test.

### DEFINITIONS
- VAGUE / INDIRECT (list its id): The user expresses a GOAL, outcome, or intent
  without naming the specific tool, operation, or exact object to act on. The agent
  must infer what to do. Examples: "My tests are flaky, can you help?"; "I want to
  understand where our latency is coming from"; "Clean up this file."
- DIRECT (omit its id): The user names the operation, tool, or exact target so
  there is little inference needed. Examples: "Run pytest on test_auth.py";
  "Read config.yaml and list the keys"; "Call the search_customers tool for 'Acme'".

### How to judge
Base your judgment primarily on the starting_prompt (how the user first phrases the
task), then the conversation_plan. A CUJ is VAGUE if the user's initial goal is
non-obvious -- expressed by intent rather than by naming the tool/operation/target.
If the request explicitly names a tool from the available tools, or spells out the
exact operation and target, it is DIRECT. When genuinely borderline, prefer DIRECT.

### Input Data
**Available Tools:** {tool_names}

**CUJs (JSON list):** Each object has "id", "starting_prompt", and
"conversation_plan".
{cujs_json}

### Output Format
Return ONLY a JSON object (no markdown, no prose) with exactly this shape, listing
only the ids of the VAGUE / INDIRECT CUJs:
{{
  "vague_ids": ["<CUJ id, copied verbatim from the input>", "..."]
}}
Every id MUST match an input CUJ id exactly. Use an empty list when no CUJ is vague.
Leave DIRECT CUJs out entirely -- any id you do not list counts as DIRECT."""


VAGUE_EXAMPLES_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        KEY_VAGUE: {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": [KEY_VAGUE],
}
