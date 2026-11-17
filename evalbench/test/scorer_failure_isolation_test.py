"""Regression tests for scorer failure isolation and public metric names."""
import contextlib
import subprocess
from types import SimpleNamespace
from unittest import mock

import pandas as pd
import pytest

from evaluator.agentevaluator import AgentEvaluator
from reporting.analyzer import _scored_rows
from scorers.comparator import Comparator, ComparisonResult
from scorers.turncount import TurnCount
from scorers import score as scoring_module
from scorers.pythonscorer import PythonScorer


def _finalize(patcher=None):
    ev = AgentEvaluator.__new__(AgentEvaluator)
    ev.config = {
        "orchestrator": "agent",
        "scorers": {"trajectory_matcher": {}, "turn_count": {}},
    }
    ev.generator = SimpleNamespace(fake_home=None)
    result = SimpleNamespace(agent_results=[], scoring_results=[])
    completed = subprocess.CompletedProcess(
        args=["offline"], returncode=0, stdout="done:B", stderr="")
    scenario = {"id": "B", "starting_prompt": "B", "expected_trajectory": []}
    with patcher if patcher is not None else contextlib.nullcontext():
        ev._finalize_scenario(
            scenario=scenario, last_result=completed,
            conversation_history=[{"user": "B", "agent": "done:B"}],
            accumulated_tools=[], accumulated_skills=[],
            eval_result=result, job_id="SCORER_FAILURE_TEST", metadata={},
            generated_error=None,
        )
    assert len(result.agent_results) == 1
    assert result.agent_results[0]["eval_id"] == "B"
    return result.agent_results[0], result.scoring_results


def _by_name(rows):
    return {x["comparator"]: x for x in rows}


def _assert_isolated_error(rows, needle):
    by_name = _by_name(rows)
    assert set(by_name) == {"trajectory_matcher", "turn_count"}
    assert by_name["trajectory_matcher"]["score"] == 100
    assert by_name["trajectory_matcher"]["comparison_error"] is None
    assert by_name["turn_count"]["score"] == 0
    assert needle in str(by_name["turn_count"]["comparison_error"])
    # Scorer errors must not contribute to aggregate metric scores.
    scored = _scored_rows(pd.DataFrame(rows))
    assert list(scored["comparator"]) == ["trajectory_matcher"]


def test_two_scorers_control():
    ev, rows = _finalize()
    assert "scoring_error" not in ev
    assert set(_by_name(rows)) == {"trajectory_matcher", "turn_count"}
    assert all(row["comparison_error"] is None for row in rows)


def test_comparator_error_is_already_recorded():
    with mock.patch.object(TurnCount, "compare", side_effect=RuntimeError("COMPARE_FAIL")):
        ev, rows = _finalize()
    assert "scoring_error" not in ev
    _assert_isolated_error(rows, "COMPARE_FAIL")


def test_constructor_error_preserves_independent_score():
    with mock.patch.object(TurnCount, "__init__", side_effect=RuntimeError("CTOR_FAIL")):
        ev, rows = _finalize()
    assert "scoring_error" not in ev
    _assert_isolated_error(rows, "CTOR_FAIL")


def test_serialization_error_preserves_independent_score():
    original = ComparisonResult.to_dict

    def fail_second(self):
        if self.comparator.name == "turn_count":
            raise RuntimeError("SERIALIZE_FAIL")
        return original(self)

    with mock.patch.object(ComparisonResult, "to_dict", new=fail_second):
        ev, rows = _finalize()
    assert "scoring_error" not in ev
    _assert_isolated_error(rows, "SERIALIZE_FAIL")


class MultiRowComparator(Comparator):
    def __init__(self, config):
        super().__init__(config)
        self.name = "turn_count"

    def compare(self, *args, **kwargs):
        return [("sub_a", 100, None), ("sub_b", 100, None)]


def test_partial_serialization_does_not_publish_half_a_multirow_result():
    orig = ComparisonResult.to_dict
    calls = [0]

    def fail_last(self):
        calls[0] += 1
        # First call: trajectory; next two: multirow turn_count.
        if calls[0] == 3:
            raise RuntimeError("SECOND_SUBROW_FAIL")
        return orig(self)

    with mock.patch.dict(scoring_module.DEFAULT_SCORERS, {"turn_count": MultiRowComparator}):
        with mock.patch.object(ComparisonResult, "to_dict", new=fail_last):
            ev, rows = _finalize()
    assert "scoring_error" not in ev
    _assert_isolated_error(rows, "SECOND_SUBROW_FAIL")
    assert not any(row["comparator"] in {"sub_a", "sub_b"} for row in rows)

# Constructor-error rows must retain the same public name as successful runs.


def _eval_item():
    return {
        "id": "B", "nl_prompt": "prompt", "golden_sql": "SELECT 1",
        "query_type": "DQL", "golden_result": None,
        "golden_error": None, "generated_sql": "SELECT 1",
        "generated_result": None, "generated_error": None,
        "dialects": ["sqlite"], "database": "db", "job_id": "PY_NAME_GATE",
    }


@pytest.mark.parametrize(
    ("config", "expected_public_name"),
    [
        ({"script_path": "scripts/quality_check.py"}, "quality_check"),
        ({"script_path": "scripts/quality_check.py", "scorer_name": "custom_quality"}, "custom_quality"),
        ({"script_path": ""}, "python_scorer"),
    ],
)
def test_python_scorer_constructor_error_matches_public_metric_name(
    config, expected_public_name
):
    experiment = {"scorers": {"python_scorer": config}, "database_configs": []}
    if config.get("script_path"):
        # Construction does not execute the named script. The normal public name
        # is the reference value that an error row must match.
        normal = scoring_module.get_scorer_instance(
            "python_scorer", config, experiment, _eval_item(), {}
        )
        assert len(normal) == 1
        assert normal[0].name == expected_public_name

    rows = []
    with mock.patch.object(
        PythonScorer, "__init__", side_effect=RuntimeError("INJECTED_PYTHON_CTOR")
    ):
        scoring_module.compare(_eval_item(), experiment, rows, global_models={})

    assert len(rows) == 1, rows
    assert rows[0]["comparator"] == expected_public_name, rows
    assert rows[0]["score"] == 0
    assert "INJECTED_PYTHON_CTOR" in str(rows[0]["comparison_error"])
