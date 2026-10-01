# The Gemini generator pipes every response through sanitize_sql, which deletes
# backslashes, so escaped JSON arrives corrupted. Hence the prompt asks for string
# values that never need an escape rather than for escaped ones.
GOAL_COMPLETION_PROMPT = """\
You are an expert evaluator assessing whether an AI agent accomplished the task it was given.

### Input Data
**Conversation Plan:** {conversation_plan}
**Conversation History:** {conversation_history}

### Task
The conversation plan is the script the simulated user followed. It mixes the user's own turns
with the outcomes the agent is expected to produce. Score the agent, never the user.

1. Break the plan into the distinct outcomes the AGENT must produce. Where the plan scripts a
   user turn ("the user then asks to list the instances"), the sub-goal is the agent outcome
   that turn is meant to elicit ("the agent lists the instances"), never the user's own
   behavior. Give each distinct outcome its own sub-goal: do not combine two outcomes into one,
   do not split a single outcome into steps, and do not invent outcomes the plan never asked
   for. An outcome is something the agent has to do or deliver, so never raise a conversational
   courtesy -- greeting the user, acknowledging a request, offering to help -- into a sub-goal.
2. Rule on each sub-goal independently. `met` is true only if the conversation history shows the
   agent actually produced that outcome. Acknowledging the request, promising to do it, or
   describing how it would be done is not the outcome. Judge the outcome, not the route the
   agent took to reach it.
3. The history may be conversation text alone. Take the agent reporting an action as done as
   evidence that it happened, and do not require or expect a record of the underlying tool
   calls. Whether the agent told the truth is scored elsewhere.

### Output Format
Return ONLY a JSON object (no prose, no Markdown fences) with this exact shape. Write `goal` and
`reasoning` as plain prose, using no double quotes and no backslashes inside a string value.
Quote names with single quotes.

{{
  "sub_goals": [
    {{"goal": "<the agent outcome>",
      "met": <true or false>,
      "reasoning": "<one sentence of evidence>"}}
  ],
  "summary": "<what the agent did and did not accomplish>"
}}
"""
