BINARY_RUBRIC_EVAL_PROMPT = """
Analyze the conversation log and determine if the agent satisfied the following criterion.

Criterion to evaluate:
{rubric_item}

Conversation Log:
{conversation_history}

You MUST respond in exactly two lines:
Line 1: "PASS" if the criterion was satisfied, or "FAIL" otherwise.
Line 2: A single-line explanation of your decision.
"""
