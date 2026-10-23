GOAL_COMPLETION_PROMPT = """\
You are an expert evaluator assessing whether an AI agent accomplished the task it was given.

### Input Data
**Conversation Plan (Goal):** {conversation_plan}
**Conversation History:** {conversation_history}

### Task
The conversation plan is the goal for the entire conversation that the simulated user follows to
ask the next prompt. The plan can describe the user's own messages and the outcomes the agent
must produce. Score the agent, never the user.

1. List the outcomes the agent must produce. Use one sub-goal for each outcome.
   - If the plan describes a user message, the sub-goal is the agent outcome that the message
     asks for. For example, "the user asks to list the instances" becomes "the agent lists the
     instances".
   - Do not merge two outcomes. Do not split one outcome into steps.
   - Do not add outcomes that the plan does not ask for.
   - Do not make a courtesy into a sub-goal, such as a greeting, an acknowledgment, or an offer
     to help.
2. Judge each sub-goal on its own. Set `met` to true only if the conversation history shows that
   the agent produced the outcome. An acknowledgment, a promise, or a description of how to do it
   is not the outcome. Judge the outcome, not the steps the agent took.
{evidence_rule}

### Output Format
Return ONLY a JSON object (no prose, no Markdown fences) with this exact shape. Write `goal` and
`reasoning` as plain prose.

{{
  "sub_goals": [
    {{"goal": "<the agent outcome>",
      "reasoning": "<one sentence of evidence>",
      "met": <true or false>}}
  ],
  "summary": "<what the agent did and did not accomplish>"
}}
"""

# JSON-mode schema for the prompt above. propertyOrdering matches the
# template field order.
GOAL_COMPLETION_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "sub_goals": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "goal": {"type": "STRING"},
                    "reasoning": {"type": "STRING"},
                    "met": {"type": "BOOLEAN"},
                },
                "required": ["goal", "reasoning", "met"],
                "propertyOrdering": ["goal", "reasoning", "met"],
            },
        },
        "summary": {"type": "STRING"},
    },
    "required": ["sub_goals", "summary"],
    "propertyOrdering": ["sub_goals", "summary"],
}

# Rule 3 of the prompt. The history has tool calls only if the scorer sets
# `include_tool_calls`.
TEXT_ONLY_EVIDENCE_RULE = """\
3. The history may be conversation text alone. Take the agent reporting an action as done as
   evidence that it happened, and do not require or expect a record of the underlying tool
   calls. Whether the agent told the truth is scored elsewhere."""

TOOL_CALLS_EVIDENCE_RULE = """\
3. Agent turns include a `tool_calls` list with each call and its result. If an outcome needs an
   action, such as creating or listing a resource, it is met only if a matching tool call
   succeeded. The agent saying that it did the action is not enough. If an agent turn has no
   `tool_calls` list, use what the agent reports in that turn."""


def build_goal_completion_prompt(
    conversation_plan: str,
    conversation_history: str,
    include_tool_calls: bool = False,
) -> str:
    """Builds the judge prompt for one scenario.

    If `include_tool_calls` is set, the judge checks actions against the tool
    calls in the history.
    """
    return GOAL_COMPLETION_PROMPT.format(
        conversation_plan=conversation_plan,
        conversation_history=conversation_history,
        evidence_rule=(TOOL_CALLS_EVIDENCE_RULE if include_tool_calls
                       else TEXT_ONLY_EVIDENCE_RULE),
    )
