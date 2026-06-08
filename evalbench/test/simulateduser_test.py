import unittest
from generators.prompts.simulateduser import SimulatedUserPromptGenerator


class TestSimulatedUserPromptGenerator(unittest.TestCase):

    def test_generate_cleans_json_history_and_last_reply(self):
        generator = SimulatedUserPromptGenerator(None, {})

        history = [
            {
                "user": "List instances",
                "agent": '{\n  "session_id": "session_1",\n  "response": "Here is the list of instances: instance-1",\n  "stats": {}\n}'
            }
        ]
        last_reply = '{\n  "session_id": "session_1",\n  "response": "What else can I help with?",\n  "stats": {}\n}'

        item = {
            "conversation_plan": "Verify listing instances",
            "history": history,
            "last_agent_reply": last_reply
        }

        result = generator.generate(item)
        prompt = result["prompt"]

        self.assertIn("Agent: Here is the list of instances: instance-1", prompt)
        self.assertNotIn("session_id", prompt)
        self.assertNotIn("stats", prompt)
        self.assertIn("# Last Agent Reply:\nWhat else can I help with?", prompt)


if __name__ == '__main__':
    unittest.main()
