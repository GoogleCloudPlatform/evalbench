"""The result of each trial of a scenario, for the Dashboard tab of the viewer."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock

import pandas as pd

# Allow running unit tests in core test environments where mesop is not installed
sys.modules.setdefault("mesop", MagicMock())

# Add viewer directory and root to sys.path
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../../viewer"))
)
sys.path.append(
    os.path.abspath(os.path.join(os.path.dirname(__file__), "../.."))
)

from viewer import trial_details
from viewer.conversations import conversation_indexes

JUDGE_LOG = (
    "1/2 success criteria met.\n"
    "[MET] The agent creates a backup — The create_backup call succeeded.\n"
    "[UNMET] The agent reports the backup id — The reply has no id.\n"
    "Summary: The agent created the backup but did not report its id.")


def _score_row(scenario, trial, comparator, score, **values):
    """Returns a scores.csv row with the labels of a multi-trial agent run."""
    row = {"id": f"{scenario}_trial_{trial}", "prompt_id": scenario,
           "trial_index": trial, "comparator": comparator, "score": score,
           "comparison_logs": None, "generated_error": None,
           "comparison_error": None}
    row.update(values)
    return row


class TestParseJudgeLog(unittest.TestCase):

    def test_reads_the_goals_and_the_summary(self):
        goals, summary = trial_details.parse_judge_log(JUDGE_LOG)

        self.assertEqual(
            [{"met": True, "goal": "The agent creates a backup",
              "reasoning": "The create_backup call succeeded."},
             {"met": False, "goal": "The agent reports the backup id",
              "reasoning": "The reply has no id."}],
            goals)
        self.assertEqual(
            "The agent created the backup but did not report its id.",
            summary)

    def test_a_line_without_a_tag_continues_the_line_before_it(self):
        goals, summary = trial_details.parse_judge_log(
            "0/1 sub-goals met.\n"
            "[UNMET] List the instances — The agent did not call a tool.\n"
            "It asked for the project.\n"
            "Summary: No tool call.\n"
            "The agent asked a question.")

        self.assertEqual(
            "The agent did not call a tool. It asked for the project.",
            goals[0]["reasoning"])
        self.assertEqual("No tool call. The agent asked a question.", summary)

    def test_a_goal_without_reasoning(self):
        goals, _ = trial_details.parse_judge_log("[MET] List the instances")

        self.assertEqual(
            [{"met": True, "goal": "List the instances", "reasoning": ""}],
            goals)

    def test_an_empty_log_has_no_goals(self):
        for log in (None, float("nan"), "", "A reply in a different format"):
            with self.subTest(log=log):
                self.assertEqual(
                    ([], ""), trial_details.parse_judge_log(log))


class TestLoadTrials(unittest.TestCase):

    def setUp(self):
        self.results_dir = tempfile.mkdtemp()

    def load(self, rows):
        pd.DataFrame(rows).to_csv(
            os.path.join(self.results_dir, trial_details.SCORES_FILE),
            index=False)
        return trial_details.load_trials(self.results_dir)

    def test_trials_are_in_the_order_of_the_trial_index(self):
        trials = self.load([
            _score_row("s1", 2, "goal_completion", 0.0),
            _score_row("s1", 0, "goal_completion", 100.0),
            _score_row("s2", 0, "goal_completion", 100.0),
            _score_row("s1", 1, "goal_completion", 50.0),
        ])

        self.assertEqual(["s1", "s2"], sorted(trials))
        self.assertEqual(
            [("s1_trial_0", 0, 100.0), ("s1_trial_1", 1, 50.0),
             ("s1_trial_2", 2, 0.0)],
            [(t["eval_id"], t["trial_index"], t["score"])
             for t in trials["s1"]])

    def test_a_trial_has_the_goals_of_the_goal_completion_row(self):
        trial = self.load([
            _score_row("s1", 0, "turn_count", 3,
                       comparison_logs="[UNMET] Not a goal"),
            _score_row("s1", 0, "goal_completion", 50.0,
                       comparison_logs=JUDGE_LOG),
        ])["s1"][0]

        self.assertEqual(50.0, trial["score"])
        self.assertEqual([True, False], [g["met"] for g in trial["goals"]])
        self.assertTrue(trial["summary"].startswith("The agent created"))
        self.assertEqual(("", ""),
                         (trial["run_error"], trial["scorer_error"]))

    def test_a_trial_with_a_scorer_error_has_no_score(self):
        trial = self.load([_score_row(
            "s1", 0, "goal_completion", None,
            comparison_error="The judge reply was not JSON.")])["s1"][0]

        self.assertIsNone(trial["score"])
        self.assertEqual("The judge reply was not JSON.",
                         trial["scorer_error"])
        self.assertEqual([], trial["goals"])

    def test_a_trial_with_an_agent_error_and_no_goal_row(self):
        trial = self.load([_score_row(
            "s1", 0, "turn_count", None,
            generated_error="The CLI timed out.")])["s1"][0]

        self.assertIsNone(trial["score"])
        self.assertEqual("The CLI timed out.", trial["run_error"])

    def test_a_trial_with_an_error_is_unscored_also_with_a_judge_score(self):
        # The job leaves this trial out of the stats in run_stats.csv.
        trial = self.load([_score_row(
            "s1", 0, "goal_completion", 100.0, comparison_logs=JUDGE_LOG,
            generated_error="Simulated user error: empty reply")])["s1"][0]

        self.assertIsNone(trial["score"])
        self.assertEqual("Simulated user error: empty reply",
                         trial["run_error"])
        self.assertEqual(2, len(trial["goals"]))

    def test_a_run_with_one_trial_has_no_trials(self):
        # A run with one trial has no trial labels.
        self.assertEqual({}, self.load([
            {"id": "s1", "comparator": "goal_completion", "score": 100.0}]))
        self.assertEqual({}, self.load([
            {"id": "s1", "prompt_id": None, "trial_index": None,
             "comparator": "goal_completion", "score": 100.0}]))

    def test_a_run_without_the_file_has_no_trials(self):
        self.assertEqual({}, trial_details.load_trials(self.results_dir))


class TestConversationIndexes(unittest.TestCase):

    def setUp(self):
        self.results_dir = tempfile.mkdtemp()

    def write(self, rows):
        pd.DataFrame(rows).to_csv(
            os.path.join(self.results_dir, "evals.csv"), index=False)

    def test_a_row_without_a_conversation_has_no_index(self):
        self.write([
            {"eval_id": "s1_trial_0", "conversation_history": "[]"},
            {"eval_id": "s2_trial_0", "conversation_history": None},
            {"eval_id": "s1_trial_1", "conversation_history": "[]"},
        ])

        self.assertEqual({"s1_trial_0": 0, "s1_trial_1": 1},
                         conversation_indexes(self.results_dir))

    def test_uses_the_id_column_without_an_eval_id_column(self):
        self.write([{"id": "s1_trial_0", "conversation_history": "[]"}])

        self.assertEqual({"s1_trial_0": 0},
                         conversation_indexes(self.results_dir))

    def test_a_run_without_conversations_has_no_indexes(self):
        self.assertEqual({}, conversation_indexes(self.results_dir))
        self.write([{"eval_id": "s1"}])
        self.assertEqual({}, conversation_indexes(self.results_dir))


if __name__ == "__main__":
    unittest.main()
