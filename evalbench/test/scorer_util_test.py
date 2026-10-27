import json
from scorers.util import filter_conversation_history_json, make_hashable


def test_make_hashable_unhashable_leaf():
    class UnhashableObj:
        __hash__ = None

        def __str__(self):
            return "unhashable-leaf"

    result = make_hashable({"nested": [UnhashableObj(), 42]})
    assert hash(result) is not None
    assert ("nested", ("unhashable-leaf", 42)) in result


def test_filter_conversation_history_json_without_tools():
    history = [
        {"user": "List cloud SQL instances.", "agent": json.dumps({"response": "Sure, looking.", "tool_calls": [{"tool_name": "list"}]})},
        {"user": "Thanks.", "agent": "No problem."}
    ]
    filtered_str = filter_conversation_history_json(history, include_tool_calls=False)
    filtered = json.loads(filtered_str)

    # First turn should have agent JSON parsed, and tool_calls key removed
    agent_1 = json.loads(filtered[0]["agent"])
    assert "tool_calls" not in agent_1
    assert agent_1["response"] == "Sure, looking."

    # Second turn was plain text, should be preserved as-is
    assert filtered[1]["agent"] == "No problem."


def test_filter_conversation_history_json_with_tools():
    history = [
        {
            "user": "List cloud SQL instances.",
            "agent": json.dumps({
                "response": "Found instance-1.",
                "tool_calls": [
                    {
                        "tool_name": "cloud-sql__list_instances",
                        "parameters": {"project": "my-project"},
                        "status": "success",
                        "response": [{"name": "instance-1", "state": "RUNNABLE"}]
                    }
                ]
            })
        }
    ]
    filtered_str = filter_conversation_history_json(history, include_tool_calls=True)
    filtered = json.loads(filtered_str)

    agent_1 = json.loads(filtered[0]["agent"])
    assert "tool_calls" in agent_1
    assert len(agent_1["tool_calls"]) == 1
    assert agent_1["tool_calls"][0]["tool_name"] == "cloud-sql__list_instances"
    assert agent_1["response"] == "Found instance-1."


def test_filter_conversation_history_json_with_dict_agent():
    history = [
        {
            "user": "List cloud SQL instances.",
            "agent": {
                "response": "Sure, looking.",
                "tool_calls": [{"tool_name": "list"}]
            }
        },
        {"user": "Thanks.", "agent": "No problem."}
    ]
    filtered_str = filter_conversation_history_json(
        history, include_tool_calls=False
    )
    filtered = json.loads(filtered_str)

    # First turn should have agent field as a serialized JSON string
    assert isinstance(filtered[0]["agent"], str)
    agent_1 = json.loads(filtered[0]["agent"])
    assert "tool_calls" not in agent_1
    assert agent_1["response"] == "Sure, looking."
