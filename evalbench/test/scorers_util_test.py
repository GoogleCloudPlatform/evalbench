import unittest
from scorers.util import format_conversation_history


class TestScorersUtil(unittest.TestCase):

    def test_format_conversation_history_without_tool_calls(self):
        history = [
            {
                "user": "List Cloud SQL instances.",
                "agent": '{"response": "Here is the list: instance-1", "tool_calls": [{"tool_name": "list_instances", "parameters": {}, "status": "success", "response": "instance-1"}]}'
            }
        ]

        formatted = format_conversation_history(history, include_tool_calls=False)
        expected = "User: List Cloud SQL instances.\nAgent: Here is the list: instance-1\n"
        self.assertEqual(formatted, expected)

    def test_format_conversation_history_with_tool_calls(self):
        history = [
            {
                "user": "List Cloud SQL instances.",
                "agent": '{"response": "Here is the list: instance-1", "tool_calls": [{"tool_name": "list_instances", "parameters": {"proj": "p1"}, "status": "success", "response": "[instance-1]"}]}'
            }
        ]

        formatted = format_conversation_history(history, include_tool_calls=True)
        expected = (
            "User: List Cloud SQL instances.\n"
            "Agent invoked list_instances({'proj': 'p1'}) -> SUCCESS:\n"
            "  [instance-1]\n"
            "Agent: Here is the list: instance-1\n"
        )
        self.assertEqual(formatted, expected)

    def test_format_conversation_history_malformed_fallback(self):
        history = [
            {
                "user": "Hello",
                "agent": "Plain text agent response"
            }
        ]

        formatted = format_conversation_history(history, include_tool_calls=True)
        expected = "User: Hello\nAgent: Plain text agent response\n"
        self.assertEqual(formatted, expected)


if __name__ == '__main__':
    unittest.main()
