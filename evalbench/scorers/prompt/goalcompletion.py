GOAL_COMPLETION_PROMPT = """
You are an expert evaluator assessing whether an AI agent successfully completed its assigned task.
Your evaluation must strictly focus on whether the agent achieved the intent stated in the conversation_plan.

### Input Data
**Conversation Plan (Goal):** {conversation_plan}
**Conversation History:** {conversation_history}

### Task
Determine if the agent successfully fulfilled the requirements and intent specified in the conversation plan.

### Output Format
Provide your response in the following single-word format (first line) followed by your reasoning:
<PASS or FAIL>
Reasoning: <Detailed analysis of why it passed or failed>
"""
