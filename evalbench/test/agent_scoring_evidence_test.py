"""Regression tests for preserving agent execution and scoring evidence."""
import subprocess
from types import SimpleNamespace
from unittest import mock

from evaluator.agentevaluator import AgentEvaluator
from scorers.turncount import TurnCount


def finalize_with(config, existing_scores=()):
    ev = AgentEvaluator.__new__(AgentEvaluator)
    ev.config = {"orchestrator": "agent", "scorers": config}
    ev.generator = SimpleNamespace(fake_home=None)
    result = SimpleNamespace(
        agent_results=[], scoring_results=list(existing_scores)
    )
    completed = subprocess.CompletedProcess(
        args=["offline"], returncode=0, stdout="done:B", stderr=""
    )
    scenario = {"id": "B", "starting_prompt": "B", "expected_trajectory": []}
    ev._finalize_scenario(
        scenario=scenario,
        last_result=completed,
        conversation_history=[{"user": "B", "agent": "done:B"}],
        accumulated_tools=[], accumulated_skills=[],
        eval_result=result, job_id="EDGE_CANDIDATE", metadata={},
        generated_error=None,
    )
    assert len(result.agent_results) == 1
    assert result.agent_results[0]["eval_id"] == "B"
    return result


def test_named_scorer_ctor_error_uses_public_name():
    config = {"turns_slot": {"type": "turn_count", "scorer_name": "custom_turns"}}
    normal = finalize_with(config)
    assert [r["comparator"] for r in normal.scoring_results] == ["custom_turns"]
    with mock.patch.object(TurnCount, "__init__", side_effect=RuntimeError("NAMED_CTOR_FAIL")):
        failed = finalize_with(config)
    assert len(failed.scoring_results) == 1
    row = failed.scoring_results[0]
    assert row["comparator"] == "custom_turns", row
    assert row["score"] == 0
    assert "NAMED_CTOR_FAIL" in str(row.get("comparison_error"))
    assert not failed.agent_results[0].get("scoring_error")


def test_fallback_does_not_publish_partial_or_erase_sibling_scores():
    existing = {
        "id": "A", "comparator": "trajectory_matcher", "score": 100,
        "comparison_error": None,
    }

    def publish_then_throw(work):
        work.scoring_results.append({
            "id": "B", "comparator": "trajectory_matcher", "score": 100,
            "comparison_error": None,
        })
        raise RuntimeError("POST_PUBLICATION_DISPATCH_CRASH")

    with mock.patch(
        "evaluator.agentevaluator.AgentScoreWork.run", new=publish_then_throw
    ):
        result = finalize_with(
            {"trajectory_matcher": {}}, existing_scores=[existing]
        )
    assert result.scoring_results == [existing], result.scoring_results
    assert "POST_PUBLICATION_DISPATCH_CRASH" in result.agent_results[0].get("scoring_error", "")
