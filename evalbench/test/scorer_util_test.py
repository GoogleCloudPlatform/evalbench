import json
from scorers.util import filter_conversation_history_json


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


def test_get_python_scorer_name():
    from scorers.util import get_python_scorer_name

    # 1. Explicit scorer_name
    cfg1 = {"scorer_name": "custom_name", "script_path": "path/to/script.py"}
    assert get_python_scorer_name(cfg1, "default_key") == "custom_name"

    # 2. Script path basename fallback
    cfg2 = {"script_path": "path/to/accuracy_judge.py"}
    assert get_python_scorer_name(cfg2, "default_key") == "accuracy_judge"

    # 3. Default key fallback
    cfg3 = {}
    assert get_python_scorer_name(cfg3, "default_key") == "default_key"

    # 4. Invalid config fallback
    assert get_python_scorer_name(None, "default_key") == "default_key"

