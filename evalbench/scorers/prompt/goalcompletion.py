GOAL_COMPLETION_PROMPT = """\
You are an expert evaluator assessing whether an AI agent accomplished the task it was given.

### Input Data
**Conversation Plan (Goal):** {conversation_plan}
**Conversation History:** {conversation_history}

### Task
The conversation plan states the goal of the whole conversation. The simulated user reads it
to decide each next message. The plan can describe the user's own messages and the outcomes
the agent must produce. Score the agent, never the user.

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

# Used in place of GOAL_COMPLETION_PROMPT when a scenario sets `success_criteria`.
GOAL_COMPLETION_CRITERIA_PROMPT = """\
You are an expert evaluator assessing whether an AI agent accomplished the task it was given.

### Input Data
**Conversation Plan (Goal):** {conversation_plan}
**Success Criteria:**
{success_criteria}
**Conversation History:** {conversation_history}

### Task
The conversation plan states the goal of the whole conversation. The simulated user reads it
to decide each next message. Score the agent, never the user.

1. The success criteria are the complete list of outcomes to judge. Use the plan only as context.
   Do not add, drop, merge, or split criteria.
2. Judge each criterion on its own. Set `met` to true only if the conversation history shows that
   the agent produced the outcome. An acknowledgment, a promise, or a description of how to do it
   is not the outcome. Judge the outcome, not the steps the agent took.
{evidence_rule}

### Output Format
Return ONLY a JSON object (no prose, no Markdown fences) with this exact shape. Write
`reasoning` as plain prose. `index` is the number of the criterion in the list above.
Return exactly one entry per criterion.

{{
  "success_criteria": [
    {{"index": <criterion number>,
      "reasoning": "<one sentence of evidence>",
      "met": <true or false>}}
  ],
  "summary": "<what the agent did and did not accomplish>"
}}
"""

# JSON-mode schemas for the prompts above. propertyOrdering matches the
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

GOAL_COMPLETION_CRITERIA_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "success_criteria": {
            "type": "ARRAY",
            "items": {
                "type": "OBJECT",
                "properties": {
                    "index": {"type": "INTEGER"},
                    "reasoning": {"type": "STRING"},
                    "met": {"type": "BOOLEAN"},
                },
                "required": ["index", "reasoning", "met"],
                "propertyOrdering": ["index", "reasoning", "met"],
            },
        },
        "summary": {"type": "STRING"},
    },
    "required": ["success_criteria", "summary"],
    "propertyOrdering": ["success_criteria", "summary"],
}

# Rule 3 of both prompts. The history has tool calls only if the scorer sets
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
    success_criteria: list[str],
    include_tool_calls: bool = False,
) -> str:
    """Builds the judge prompt for one scenario.

    Returns GOAL_COMPLETION_CRITERIA_PROMPT if `success_criteria` is non-empty,
    else GOAL_COMPLETION_PROMPT. If `include_tool_calls` is set, the judge
    checks actions against the tool calls in the history.
    """
    evidence_rule = (TOOL_CALLS_EVIDENCE_RULE if include_tool_calls
                     else TEXT_ONLY_EVIDENCE_RULE)
    if not success_criteria:
        return GOAL_COMPLETION_PROMPT.format(
            conversation_plan=conversation_plan,
            conversation_history=conversation_history,
            evidence_rule=evidence_rule,
        )
    return GOAL_COMPLETION_CRITERIA_PROMPT.format(
        conversation_plan=conversation_plan,
        success_criteria="\n".join(
            f"{i}. {c}" for i, c in enumerate(success_criteria, 1)),
        conversation_history=conversation_history,
        evidence_rule=evidence_rule,
    )
