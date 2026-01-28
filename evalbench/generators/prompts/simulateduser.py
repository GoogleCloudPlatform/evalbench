from .generator import PromptGenerator

SIMULATED_USER_PROMPT = """You are a simulated user interacting with Gemini CLI agent.
Your objective is to follow the conversation plan provided below and engage with the agent naturally to achieve the goals.

# Conversation Plan:
[[conversation_plan]]

# Interaction History:
[[history]]

# Last Agent Reply:
[[last_agent_reply]]

Based on the plan and the agent's last reply, provide your next input to the CLI agent.
If the plan is fully completed or you cannot proceed, reply with "TERMINATE".
Only provide the text command or response. Do not include markdown formatting or explanations unless necessary for the command.
"""


class SimulatedUserPromptGenerator(PromptGenerator):
    def __init__(self, db, promptgenerator_config):
        super().__init__(db, promptgenerator_config)
        self.prompt_template = SIMULATED_USER_PROMPT

    def setup(self):
        pass

    def generate(self, item):
        # item is the payload dictionary
        plan = item.get("conversation_plan", "")
        history_list = item.get("history", [])
        last_reply = item.get("last_agent_reply", "")

        # Format history
        history_str = ""
        for turn in history_list:
            history_str += f"User: {turn['user']}\nAgent: {turn['agent']}\n"

        prompt = self.prompt_template.replace("[[conversation_plan]]", str(plan))
        prompt = prompt.replace("[[history]]", history_str)
        prompt = prompt.replace("[[last_agent_reply]]", str(last_reply))

        item["prompt"] = prompt
        return item
