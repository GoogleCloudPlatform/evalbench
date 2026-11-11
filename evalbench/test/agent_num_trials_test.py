import json
import os
import subprocess
import threading
import time
import types
from unittest.mock import MagicMock, patch

import pytest

from evaluator.agentevaluator import AgentEvaluator
from generators.models.agent_cli import AgentCliGenerator
from work.agentscorework import AgentScoreWork


@pytest.fixture
def make_evaluator(tmp_path):
    """Returns a factory for an AgentEvaluator with a mocked agent CLI."""
    patcher = patch("evaluator.agentevaluator.get_generator")
    mock_get_generator = patcher.start()
    generator = MagicMock(spec=AgentCliGenerator)
    generator.version = "1.0"
    # A real directory: with more than one trial, the evaluator resets it.
    fake_home = tmp_path / "fake_home"
    fake_home.mkdir()
    generator.fake_home = str(fake_home)
    mock_get_generator.return_value = generator

    def _make(num_trials, agent_runners=1, **config):
        return AgentEvaluator({
            "model_config": "dummy.yaml",
            "num_trials": num_trials,
            "runners": {"agent_runners": agent_runners},
            **config,
        })

    yield _make
    patcher.stop()


def _item(payload):
    return types.SimpleNamespace(
        payload=json.dumps(payload), agent_results=[], scoring_results=[]
    )


def _ids(item):
    return [s["id"] for s in json.loads(item.payload)["scenarios"]]


def test_one_trial_returns_item_unchanged(make_evaluator):
    item = _item({"scenarios": [{"id": "s1"}]})
    assert make_evaluator(num_trials=1)._trial_item(item, 0) is item


def test_trial_item_labels_scenarios(make_evaluator):
    item = _item({"scenarios": [{"id": "s1"}, {"id": "s2"}]})
    trial_item = make_evaluator(num_trials=3)._trial_item(item, 1)

    scenarios = json.loads(trial_item.payload)["scenarios"]
    assert scenarios == [
        {"id": "s1_trial_1", "prompt_id": "s1", "trial_index": 1},
        {"id": "s2_trial_1", "prompt_id": "s2", "trial_index": 1},
    ]
    assert trial_item.agent_results == []
    assert _ids(item) == ["s1", "s2"]  # The source item is not changed.


def test_trial_item_keeps_single_scenario_payload(make_evaluator):
    item = _item({"id": "s1"})
    trial_item = make_evaluator(num_trials=2)._trial_item(item, 0)
    assert json.loads(trial_item.payload)["id"] == "s1_trial_0"


def test_scenario_num_trials_lowers_the_count(make_evaluator):
    item = _item({"scenarios": [{"id": "once", "num_trials": 1}, {"id": "s2"}]})
    evaluator = make_evaluator(num_trials=2)
    assert _ids(evaluator._trial_item(item, 0)) == ["once_trial_0", "s2_trial_0"]
    assert _ids(evaluator._trial_item(item, 1)) == ["s2_trial_1"]


def test_scenario_num_trials_above_run_value_is_capped(make_evaluator, caplog):
    item = _item({"scenarios": [{"id": "s1", "num_trials": 5}]})
    evaluator = make_evaluator(num_trials=2)
    assert _ids(evaluator._trial_item(item, 1)) == ["s1_trial_1"]
    evaluator._trial_item(item, 0)
    assert "above the run value 2" in caplog.text


def test_item_with_no_scenario_in_trial_is_skipped(make_evaluator):
    item = _item({"scenarios": [{"id": "once", "num_trials": 1}]})
    assert make_evaluator(num_trials=2)._trial_item(item, 1) is None


@pytest.mark.parametrize("value", [0, -1, "3", 2.0, True])
def test_invalid_run_num_trials_raises(make_evaluator, value):
    with pytest.raises(ValueError, match="the run config"):
        make_evaluator(num_trials=value)


def test_invalid_scenario_num_trials_raises(make_evaluator):
    item = _item({"scenarios": [{"id": "bad", "num_trials": 0}]})
    with pytest.raises(ValueError, match="scenario bad"):
        make_evaluator(num_trials=2)._trial_item(item, 0)


@patch("evaluator.agentevaluator.SimulatedUser")
def test_trials_run_one_after_another(mock_sim_user, make_evaluator):
    evaluator = make_evaluator(num_trials=2, agent_runners=4)
    events = []
    lock = threading.Lock()

    def fake_process_scenario(scenario, eval_result, *args):
        with lock:
            events.append(scenario["trial_index"])
        # Hold one trial 0 scenario. Without a barrier between trials, the
        # free runners start trial 1 before this scenario ends.
        if scenario["id"] == "a_trial_0":
            time.sleep(0.2)
        eval_result.agent_results.append({"eval_id": scenario["id"]})
        with lock:
            events.append(scenario["trial_index"])

    evaluator.process_scenario = fake_process_scenario
    dataset = [_item({"scenarios": [{"id": "a"}]}),
               _item({"scenarios": [{"id": "b"}]})]
    eval_outputs, _ = evaluator._evaluate_agent_cli(dataset, "job1", None)

    # Every start and end of trial 0 comes before any start of trial 1.
    assert events == [0, 0, 0, 0, 1, 1, 1, 1]
    assert sorted(o["eval_id"] for o in eval_outputs) == [
        "a_trial_0", "a_trial_1", "b_trial_0", "b_trial_1"]


@pytest.mark.parametrize("scenario, expected", [
    ({"id": "s1_trial_2", "prompt_id": "s1", "trial_index": 2},
     {"prompt_id": "s1", "trial_index": 2}),
    ({"id": "s1"}, {}),
], ids=["multi_trial", "single_trial"])
def test_finalize_scenario_labels_eval_row(make_evaluator, scenario, expected):
    evaluator = make_evaluator(num_trials=3)
    eval_result = _item({})
    with patch("evaluator.agentevaluator.AgentScoreWork"):
        evaluator._finalize_scenario(
            {"starting_prompt": "p", **scenario},
            subprocess.CompletedProcess([], 0, "", ""),
            [], [], [], eval_result, "job1", {})

    eval_row = eval_result.agent_results[0]
    assert eval_row["eval_id"] == scenario["id"]
    assert {k: eval_row[k] for k in expected} == expected
    assert ("trial_index" in eval_row) == bool(expected)


def test_score_rows_get_trial_labels():
    earlier_row = {"id": "other"}
    scoring_results = [earlier_row]
    score_work = AgentScoreWork(
        config={},
        eval_output={"eval_id": "s1_trial_1", "prompt_id": "s1",
                     "trial_index": 1, "scenario": {}, "metadata": {}},
        scoring_results=scoring_results,
    )
    with patch("scorers.score.compare") as mock_compare:
        mock_compare.side_effect = lambda **kw: kw["scoring_results"].append(
            {"id": "s1_trial_1", "comparator": "goal_completion"})
        score_work.run()

    assert scoring_results[1]["prompt_id"] == "s1"
    assert scoring_results[1]["trial_index"] == 1
    assert earlier_row == {"id": "other"}


def _run_and_record(evaluator, dataset):
    """Runs the trials with a fake scenario and returns what each one saw.

    The fake scenario records the file names in its directory, then writes a
    file there. The directory is the scenario's work dir, or the sandbox home
    if it has none.
    """
    seen = []

    def fake_process_scenario(scenario, eval_result, *args):
        directory = (scenario.get("resolved_work_dir")
                     or evaluator.generator.fake_home)
        os.makedirs(directory, exist_ok=True)
        seen.append((scenario["id"], sorted(os.listdir(directory))))
        with open(os.path.join(directory, "output.txt"), "w") as f:
            f.write(scenario["id"])

    evaluator.process_scenario = fake_process_scenario
    with patch("evaluator.agentevaluator.SimulatedUser"):
        evaluator._evaluate_agent_cli(dataset, "job1", None)
    return seen


def test_home_is_reset_between_trials(make_evaluator, tmp_path):
    evaluator = make_evaluator(num_trials=3)
    seen = _run_and_record(evaluator, [_item({"scenarios": [{"id": "a"}]})])

    # No trial sees the file that the trial before it wrote.
    assert seen == [("a_trial_0", []), ("a_trial_1", []), ("a_trial_2", [])]
    # The snapshot is deleted when the run ends.
    assert sorted(os.listdir(tmp_path)) == ["fake_home"]


def test_home_is_not_reset_with_one_trial(make_evaluator, tmp_path):
    evaluator = make_evaluator(num_trials=1)
    with patch("evaluator.agentevaluator.TrialSnapshot") as mock_snapshot:
        _run_and_record(evaluator, [_item({"scenarios": [{"id": "a"}]})])

    mock_snapshot.assert_not_called()
    assert os.listdir(tmp_path / "fake_home") == ["output.txt"]


def test_work_dir_is_reset_between_trials(make_evaluator, tmp_path):
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    (work_dir / "starter.sql").write_text("select 1")
    scenario = {"id": "a", "resolved_work_dir": str(work_dir)}
    evaluator = make_evaluator(num_trials=2)
    seen = _run_and_record(evaluator, [_item({"scenarios": [scenario]})])

    assert seen == [("a_trial_0", ["starter.sql"]), ("a_trial_1", ["starter.sql"])]


def test_work_dir_of_single_trial_scenario_is_not_reset(make_evaluator, tmp_path):
    once = {"id": "once", "num_trials": 1,
            "resolved_work_dir": str(tmp_path / "once")}
    twice = {"id": "twice", "resolved_work_dir": str(tmp_path / "twice")}
    evaluator = make_evaluator(num_trials=2)
    _run_and_record(evaluator, [_item({"scenarios": [once, twice]})])

    # The scenario that ran once keeps its output. It needs no reset.
    assert os.listdir(tmp_path / "once") == ["output.txt"]


def test_snapshot_is_removed_when_a_trial_fails(make_evaluator, tmp_path):
    evaluator = make_evaluator(num_trials=2)
    with patch.object(evaluator, "_run_items", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            evaluator._evaluate_agent_cli(
                [_item({"scenarios": [{"id": "a"}]})], "job1", None)

    assert sorted(os.listdir(tmp_path)) == ["fake_home"]


def test_unsafe_work_dir_raises_before_any_trial(make_evaluator, tmp_path):
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    # A work_dir of "." is the directory of the evalset file.
    scenario = {"id": "a", "resolved_work_dir": str(dataset_dir)}
    evaluator = make_evaluator(
        num_trials=2, dataset_config=str(dataset_dir / "evalset.json"))
    evaluator.process_scenario = MagicMock()

    with pytest.raises(ValueError, match="cannot be reset between trials"):
        evaluator._evaluate_agent_cli(
            [_item({"scenarios": [scenario]})], "job1", None)

    evaluator.process_scenario.assert_not_called()
    assert sorted(os.listdir(tmp_path)) == ["dataset", "fake_home"]


def test_unsafe_work_dir_is_allowed_with_one_trial(make_evaluator, tmp_path):
    dataset_dir = tmp_path / "dataset"
    dataset_dir.mkdir()
    scenario = {"id": "a", "num_trials": 1,
                "resolved_work_dir": str(dataset_dir)}
    evaluator = make_evaluator(
        num_trials=2, dataset_config=str(dataset_dir / "evalset.json"))
    seen = _run_and_record(evaluator, [_item({"scenarios": [scenario]})])

    assert seen == [("a_trial_0", [])]
