"""The stats across trials on the Dashboard tab of the viewer."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, call, patch

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

from viewer import dashboard

# Use the module object that the dashboard imported. A patch of `me` on this
# object then also applies when the dashboard calls the component.
run_stats = dashboard.run_stats


def _stats_row(scenario_id, passed, scored, unscored=0, **values):
    """Returns a run_stats.csv row with the columns that the viewer reads.

    Without `values`, the pass stats are empty, as for a scenario with no
    scored trial.
    """
    row = {"scenario_id": scenario_id, "num_trials": scored + unscored,
           "scored_trials": scored, "unscored_trials": unscored,
           "passed_trials": passed, "pass_rate": None, "flakiness": None,
           "flaky": None, "confidence": None, "interval_low": None,
           "interval_high": None, "status": "ERROR"}
    row.update(values)
    return row


STABLE_PASS = _stats_row(
    "list-instances", 3, 3, pass_rate=1.0, flakiness=0.0, flaky=False,
    confidence=0.3439, interval_low=0.4385, interval_high=1.0, status="PASS")
FLAKY = _stats_row(
    "create-backup", 2, 3, pass_rate=0.6667, flakiness=0.8889, flaky=True,
    confidence=0.0523, interval_low=0.2077, interval_high=0.9385,
    status="FAIL")
STABLE_FAILURE = _stats_row(
    "delete-user", 0, 3, pass_rate=0.0, flakiness=0.0, flaky=False,
    confidence=0.0001, interval_low=0.0, interval_high=0.5615, status="FAIL")
NO_SCORED_TRIAL = _stats_row("clone-instance", None, 0, unscored=3)

SUITE_SUMMARY = [
    {"metric_name": "goal_completion", "correct_results_count": 1,
     "total_results_count": 4, "pass_rate": 0.5556, "interval_low": 0.2667,
     "interval_high": 0.8112, "flakiness": 0.2963, "confidence": 0.1321,
     "flaky_scenarios": 1},
    {"metric_name": "turn_count", "correct_results_count": 4,
     "total_results_count": 4},
]


class RunStatsTestCase(unittest.TestCase):

    def setUp(self):
        self.results_dir = tempfile.mkdtemp()

    def write(self, name, rows):
        pd.DataFrame(rows).to_csv(
            os.path.join(self.results_dir, name), index=False)

    def load(self, rows):
        """Returns `rows` after a round trip through run_stats.csv."""
        self.write(run_stats.RUN_STATS_FILE, rows)
        return run_stats.load_run_stats(self.results_dir)


class TestFormat(unittest.TestCase):

    def test_formats_values(self):
        for expected, actual in [
            ("67%", run_stats.format_rate(0.6667)),
            ("100%", run_stats.format_rate(1)),
            ("–", run_stats.format_rate(None)),
            ("–", run_stats.format_rate(float("nan"))),
            ("–", run_stats.format_rate("N/A")),
            ("21% to 94%", run_stats.format_interval(0.2077, 0.9385)),
            ("–", run_stats.format_interval(0.2, None)),
            ("0.89", run_stats.format_score(0.8889)),
            ("–", run_stats.format_score(pd.NA)),
        ]:
            with self.subTest(expected=expected):
                self.assertEqual(expected, actual)


class TestLoadRunStats(RunStatsTestCase):

    def test_a_run_without_the_file_has_no_stats(self):
        self.assertIsNone(run_stats.load_run_stats(self.results_dir))

    def test_a_file_without_the_stats_columns_has_no_stats(self):
        self.assertIsNone(self.load([{"scenario_id": "s1", "other": 1}]))

    def test_a_file_that_is_not_a_csv_has_no_stats(self):
        with open(os.path.join(
                self.results_dir, run_stats.RUN_STATS_FILE), "w") as f:
            f.write("")

        self.assertIsNone(run_stats.load_run_stats(self.results_dir))


class TestScenarioRows(RunStatsTestCase):

    def test_flaky_scenarios_come_first_and_passes_last(self):
        rows = run_stats.scenario_rows(self.load(
            [STABLE_PASS, NO_SCORED_TRIAL, STABLE_FAILURE, FLAKY]))

        self.assertEqual(
            ["create-backup", "delete-user", "clone-instance",
             "list-instances"],
            [row["scenario_id"] for row in rows])

    def test_values_of_a_flaky_scenario(self):
        row = run_stats.scenario_rows(self.load([STABLE_PASS, FLAKY]))[0]

        self.assertTrue(row["flaky"])
        self.assertEqual("FAIL", row["status"])
        self.assertEqual((2, 1, 0),
                         (row["passed"], row["failed"], row["unscored"]))
        self.assertEqual("2/3", row["trials"])
        self.assertEqual("67%", row["pass_rate"])
        self.assertEqual("21% to 94%", row["interval"])
        self.assertEqual("0.89", row["flakiness"])
        self.assertEqual("0.05", row["confidence"])

    def test_a_scenario_with_no_scored_trial_has_empty_pass_stats(self):
        row = run_stats.scenario_rows(self.load([NO_SCORED_TRIAL]))[0]

        self.assertFalse(row["flaky"])
        self.assertEqual("ERROR", row["status"])
        self.assertIsNone(row["passed"])
        self.assertIsNone(row["failed"])
        self.assertEqual("3 unscored", row["trials"])
        for name in ("pass_rate", "interval", "flakiness", "confidence"):
            self.assertEqual("–", row[name])

    def test_a_scenario_with_an_unscored_trial_shows_both_counts(self):
        row = run_stats.scenario_rows(self.load([_stats_row(
            "s1", 1, 2, unscored=1, pass_rate=0.5, flakiness=1.0,
            flaky=True, status="ERROR")]))[0]

        self.assertEqual("1/2, 1 unscored", row["trials"])
        self.assertEqual((1, 1, 1),
                         (row["passed"], row["failed"], row["unscored"]))

    def test_a_job_without_the_pass_metric_has_an_empty_status(self):
        row = run_stats.scenario_rows(self.load(
            [_stats_row("s1", None, 3, status=None)]))[0]

        self.assertEqual("–", row["status"])
        self.assertEqual("–", row["trials"])


class TestSuiteStats(RunStatsTestCase):

    def test_reads_the_pass_metric_row(self):
        stats = run_stats.suite_stats(pd.DataFrame(SUITE_SUMMARY))

        self.assertEqual(
            {"pass_rate": 0.5556, "interval_low": 0.2667,
             "interval_high": 0.8112, "flakiness": 0.2963,
             "confidence": 0.1321},
            stats)

    def test_a_summary_without_the_stats_gives_no_entries(self):
        for summary in (
            None,
            pd.DataFrame(),
            pd.DataFrame([{"metric_name": "turn_count"}]),
            pd.DataFrame([{"metric_name": "goal_completion",
                           "correct_results_count": 1}]),
            pd.DataFrame([{"metric_name": "goal_completion",
                           "pass_rate": float("nan")}]),
        ):
            with self.subTest(summary=summary):
                self.assertEqual({}, run_stats.suite_stats(summary))

    def test_trials_summary(self):
        self.assertEqual(
            "2 scenarios, 3 trials each",
            run_stats.trials_summary(self.load([STABLE_PASS, FLAKY])))
        self.assertEqual(
            "2 scenarios, 2 to 3 trials each",
            run_stats.trials_summary(self.load(
                [STABLE_PASS, _stats_row("s2", 2, 2, status="PASS")])))
        self.assertEqual(
            "1 scenario, 3 trials each",
            run_stats.trials_summary(self.load([STABLE_PASS])))

    def test_cards_without_the_suite_stats_show_the_flaky_count_only(self):
        rows = run_stats.scenario_rows(self.load([STABLE_PASS, FLAKY]))

        cards = run_stats.suite_cards({}, rows)

        self.assertEqual([("Flaky scenarios", "1 of 2")],
                         [card[:2] for card in cards])


class TestSuiteStatsComponent(RunStatsTestCase):
    """The stats of the suite on the Dashboard tab."""

    def texts(self, summary=None):
        with patch.object(run_stats, "me") as me:
            run_stats.suite_stats_component(self.results_dir, summary)
        return [c.args[0] for c in me.text.call_args_list if c.args]

    def test_draws_nothing_without_the_file(self):
        self.assertEqual([], self.texts(pd.DataFrame(SUITE_SUMMARY)))

    def test_draws_the_suite_cards_and_no_scenario(self):
        self.write(run_stats.RUN_STATS_FILE,
                   [STABLE_PASS, FLAKY, STABLE_FAILURE, NO_SCORED_TRIAL])

        texts = self.texts(pd.DataFrame(SUITE_SUMMARY))

        for expected in [
            "Stats across trials",
            "4 scenarios, 3 trials each. "
            "The Stability tab shows each scenario.",
            "Pass rate", "56%", "95% interval: 27% to 81%",
            "Flaky scenarios", "1 of 4", "0.30", "0.13",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, texts)
        # The scenarios are on the Stability tab.
        self.assertNotIn("create-backup", texts)


class TestStabilityComponent(RunStatsTestCase):
    """The stats of each scenario on the Stability tab."""

    def render(self):
        with patch.object(run_stats, "me") as me:
            run_stats.stability_component(self.results_dir)
        return me

    def texts(self, me):
        return [c.args[0] for c in me.text.call_args_list if c.args]

    def test_draws_the_scenarios_with_the_flaky_scenario_first(self):
        self.write(run_stats.RUN_STATS_FILE,
                   [STABLE_PASS, FLAKY, STABLE_FAILURE, NO_SCORED_TRIAL])

        texts = self.texts(self.render())

        for expected in [
            "Stability",
            "4 scenarios, 3 trials each. "
            "Select a scenario to see the result of each trial.",
            "create-backup", "2/3", "67%", "21% to 94%", "0.89",
            "clone-instance", "3 unscored", "ERROR",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, texts)
        self.assertLess(texts.index("create-backup"),
                        texts.index("list-instances"))
        # The suite cards are on the Dashboard tab.
        self.assertNotIn("Flaky scenarios", texts)

    def test_each_column_but_the_scenario_has_a_tooltip(self):
        self.write(run_stats.RUN_STATS_FILE, [STABLE_PASS])

        me = self.render()

        tips = [c.kwargs["message"] for c in me.tooltip.call_args_list]
        self.assertEqual(6, len(tips))
        for expected in ["at least one trial was unscored",
                         "does not count as a pass or as a failure",
                         "Passed trials divided by scored trials",
                         "Wilson interval", "4p(1 - p)", "above 90%"]:
            with self.subTest(expected=expected):
                self.assertTrue(any(expected in tip for tip in tips))

    def test_a_run_without_the_file_gets_a_message(self):
        self.assertEqual(["This run has no stats across trials."],
                         self.texts(self.render()))

    def test_only_a_run_with_the_file_has_the_tab(self):
        self.assertFalse(run_stats.has_run_stats(self.results_dir))

        self.write(run_stats.RUN_STATS_FILE, [STABLE_PASS])

        self.assertTrue(run_stats.has_run_stats(self.results_dir))


def _score_row(scenario, trial, score, **values):
    """Returns the goal_completion row of a trial in scores.csv."""
    row = {"id": f"{scenario}_trial_{trial}", "prompt_id": scenario,
           "trial_index": trial, "comparator": "goal_completion",
           "score": score, "comparison_logs": None, "generated_error": None,
           "comparison_error": None}
    row.update(values)
    return row


class TestTrialDetails(RunStatsTestCase):
    """A click on a scenario row opens the result of each trial."""

    def setUp(self):
        super().setUp()
        self.write(run_stats.RUN_STATS_FILE, [STABLE_PASS, FLAKY])
        self.write("scores.csv", [
            _score_row("list-instances", 0, 100.0),
            _score_row(
                "create-backup", 0, 100.0,
                comparison_logs="2/2 success criteria met.\n"
                "[MET] The agent creates a backup — The call succeeded.\n"
                "[MET] The agent reports the backup id — The reply has it.\n"
                "Summary: The agent did both steps."),
            _score_row(
                "create-backup", 1, 50.0,
                comparison_logs="1/2 success criteria met.\n"
                "[MET] The agent creates a backup — The call succeeded.\n"
                "[UNMET] The agent reports the backup id — "
                "The reply has no id."),
            _score_row(
                "create-backup", 2, None,
                comparison_error="The judge reply was not JSON."),
        ])
        # The row without a conversation has no index in the Conversations
        # tab, so the indexes are 0, 1, 2, and 3.
        self.write("evals.csv", [
            {"eval_id": "list-instances_trial_0",
             "conversation_history": "[]"},
            {"eval_id": "create-backup_trial_0",
             "conversation_history": "[]"},
            {"eval_id": "list-instances_trial_1",
             "conversation_history": None},
            {"eval_id": "create-backup_trial_1",
             "conversation_history": "[]"},
            {"eval_id": "create-backup_trial_2",
             "conversation_history": "[]"},
        ])
        self.flaky_key = run_stats.row_key(self.results_dir, "create-backup")

    def render(self, expanded, on_open_conversation=None):
        """Draws the component with the rows in `expanded` open."""
        with patch.object(run_stats, "me") as me:
            me.state.return_value.expanded = expanded
            run_stats.stability_component(
                self.results_dir, on_open_conversation)
        return me

    def texts(self, me):
        return [c.args[0] for c in me.text.call_args_list if c.args]

    def test_a_closed_row_shows_no_trials_and_reads_no_trial_file(self):
        with patch.object(run_stats.trial_details, "load_trials") as load, \
                patch.object(run_stats, "conversation_indexes") as indexes:
            me = self.render([], MagicMock())

        load.assert_not_called()
        indexes.assert_not_called()
        self.assertNotIn("Trial 1", self.texts(me))

    def test_an_open_row_shows_the_result_of_each_trial(self):
        texts = self.texts(self.render([self.flaky_key]))

        for expected in [
            "Trial 1", "Score 100", "create-backup_trial_0",
            "Summary: The agent did both steps.",
            "Trial 2", "Score 50", "create-backup_trial_1",
            "The agent reports the backup id", "The reply has no id.",
            "Trial 3", "Unscored", "create-backup_trial_2",
            "Scorer error: The judge reply was not JSON.",
        ]:
            with self.subTest(expected=expected):
                self.assertIn(expected, texts)
        # The other scenario stays closed.
        self.assertNotIn("list-instances_trial_0", texts)

    def test_a_trial_with_an_error_shows_the_error_and_no_score(self):
        # The judge gave a score, but the job does not count the trial.
        self.write("scores.csv", [_score_row(
            "create-backup", 0, 100.0,
            generated_error="Simulated user error: empty reply")])

        texts = self.texts(self.render([self.flaky_key]))

        self.assertIn("Unscored", texts)
        self.assertIn("Error: Simulated user error: empty reply", texts)
        self.assertNotIn("Score 100", texts)

    def test_a_goal_has_an_icon_that_tells_if_it_is_met(self):
        me = self.render([self.flaky_key])

        icons = [c.args[0] for c in me.icon.call_args_list if c.args]
        self.assertEqual(3, icons.count("check_circle"))
        self.assertEqual(1, icons.count("cancel"))
        # The open row has a different arrow from the closed row.
        self.assertEqual(1, icons.count("expand_more"))
        self.assertEqual(1, icons.count("chevron_right"))

    def test_an_open_row_has_a_button_for_each_conversation(self):
        make_handler = MagicMock()

        me = self.render([self.flaky_key], make_handler)

        self.assertEqual([call(1), call(2), call(3)],
                         make_handler.call_args_list)
        self.assertEqual(
            ["Open conversation"] * 3,
            [c.args[0] for c in me.button.call_args_list])
        self.assertEqual(
            [make_handler.return_value] * 3,
            [c.kwargs["on_click"] for c in me.button.call_args_list])

    def test_an_open_row_has_no_button_without_the_handler(self):
        me = self.render([self.flaky_key])

        me.button.assert_not_called()

    def test_a_trial_without_a_conversation_has_no_button(self):
        self.write("evals.csv", [{"eval_id": "create-backup_trial_1",
                                  "conversation_history": "[]"}])
        make_handler = MagicMock()

        self.render([self.flaky_key], make_handler)

        self.assertEqual([call(0)], make_handler.call_args_list)

    def test_the_key_of_a_different_run_opens_no_row(self):
        texts = self.texts(self.render(["other-run/create-backup"]))

        self.assertNotIn("Trial 1", texts)

    def test_a_trial_without_rows_in_the_scores_file_is_reported(self):
        self.write("scores.csv", [_score_row("create-backup", 0, 100.0)])

        texts = self.texts(self.render([self.flaky_key]))

        self.assertIn("Trial 1", texts)
        self.assertIn("scores.csv has no result for 2 of 3 trials.", texts)

    def test_a_click_opens_a_row_and_a_second_click_closes_it(self):
        state = MagicMock()
        state.expanded = []
        with patch.object(run_stats, "me") as me:
            me.state.return_value = state

            run_stats._on_toggle_scenario(MagicMock(key="run/s1"))
            self.assertEqual(["run/s1"], state.expanded)

            run_stats._on_toggle_scenario(MagicMock(key="run/s1"))
            self.assertEqual([], state.expanded)

    def test_the_row_key_has_the_run_directory_and_the_scenario(self):
        self.assertEqual("run-a/s1", run_stats.row_key("/results/run-a", "s1"))
        self.assertEqual("run-a/s1", run_stats.row_key("/results/run-a/", "s1"))

    def test_the_score_color_is_the_color_of_the_conversations_tab(self):
        for score, expected in [
            (100.0, ("Score 100", run_stats._GREEN)),
            (66.7, ("Score 66.7", run_stats._AMBER)),
            (0.0, ("Score 0", run_stats._RED)),
            (None, ("Unscored", run_stats._GREY)),
        ]:
            with self.subTest(score=score):
                self.assertEqual(
                    expected, run_stats.trial_score({"score": score}))


class TestNumericMetrics(RunStatsTestCase):
    """The min, median, and max of the numeric metrics."""

    METRICS = {"token_consumption_min": 76542.3333,
               "token_consumption_median": 113907.6667,
               "token_consumption_max": 135533.0,
               "turn_count_min": 1.0, "turn_count_median": 1.6667,
               "turn_count_max": 2.0}
    SUMMARY = SUITE_SUMMARY + [
        {"metric_name": "token_consumption", "correct_results_count": 9,
         "total_results_count": 9, "trial_min": 76542.3333,
         "trial_median": 113907.6667, "trial_max": 135533.3333}]

    def setUp(self):
        super().setUp()
        # The second scenario has no metric values.
        self.write(run_stats.RUN_STATS_FILE, [
            dict(FLAKY, **self.METRICS),
            dict(NO_SCORED_TRIAL, **dict.fromkeys(self.METRICS))])
        self.write("scores.csv", [_score_row("create-backup", 0, 100.0)])

    def texts(self, me):
        return [c.args[0] for c in me.text.call_args_list if c.args]

    def test_formats_numbers(self):
        for value, expected in [
            (76542.3333, "76,542"), (135533.0, "135,533"), (1.6667, "1.7"),
            (2.0, "2"), (99.94, "99.9"), (0, "0"), (None, "–"), ("N/A", "–"),
        ]:
            with self.subTest(value=value):
                self.assertEqual(expected, run_stats.format_number(value))

    def test_a_metric_has_a_min_a_median_and_a_max_column(self):
        self.assertEqual(
            ["token_consumption", "turn_count"],
            run_stats.metric_names([
                "scenario_id", "token_consumption_min",
                "token_consumption_median", "token_consumption_max",
                "turn_count_min", "turn_count_median", "turn_count_max",
                # Not a metric: no median and no max.
                "other_min", "interval_low", "score_mean"]))

    def test_a_scenario_row_has_its_metrics(self):
        rows = run_stats.scenario_rows(
            run_stats.load_run_stats(self.results_dir))

        self.assertEqual(
            [("token_consumption", "76,542", "113,908", "135,533"),
             ("turn_count", "1", "1.7", "2")],
            rows[0]["metrics"])
        self.assertEqual([], rows[1]["metrics"])

    def test_the_suite_metrics_come_from_the_summary(self):
        self.assertEqual(
            [("token_consumption", "76,542", "113,908", "135,533")],
            run_stats.suite_metrics(pd.DataFrame(self.SUMMARY)))

    def test_a_summary_without_the_trial_columns_has_no_suite_metrics(self):
        for summary in (None, pd.DataFrame(), pd.DataFrame(SUITE_SUMMARY)):
            with self.subTest(summary=summary):
                self.assertEqual([], run_stats.suite_metrics(summary))

    def test_the_dashboard_shows_the_suite_metrics(self):
        with patch.object(run_stats, "me") as me:
            run_stats.suite_stats_component(
                self.results_dir, pd.DataFrame(self.SUMMARY))

        texts = self.texts(me)
        for expected in ["Metrics across trials", "Metric", "Min", "Median",
                         "Max", "token_consumption", "76,542", "113,908",
                         "135,533"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, texts)

    def test_the_dashboard_shows_no_metrics_without_the_values(self):
        with patch.object(run_stats, "me") as me:
            run_stats.suite_stats_component(
                self.results_dir, pd.DataFrame(SUITE_SUMMARY))

        self.assertNotIn("Metrics across trials", self.texts(me))

    def test_an_open_row_shows_the_metrics_of_the_scenario(self):
        key = run_stats.row_key(self.results_dir, "create-backup")
        with patch.object(run_stats, "me") as me:
            me.state.return_value.expanded = [key]
            run_stats.stability_component(self.results_dir)

        texts = self.texts(me)
        for expected in ["Metrics", "token_consumption", "76,542",
                         "turn_count", "1.7", "Trial 1"]:
            with self.subTest(expected=expected):
                self.assertIn(expected, texts)

    def test_an_open_row_without_metric_values_shows_no_metrics(self):
        key = run_stats.row_key(self.results_dir, "clone-instance")
        with patch.object(run_stats, "me") as me:
            me.state.return_value.expanded = [key]
            run_stats.stability_component(self.results_dir)

        self.assertNotIn("Metrics", self.texts(me))


class TestDashboardShowsSuiteStats(RunStatsTestCase):

    def texts(self):
        with patch.object(dashboard, "me"), \
                patch.object(run_stats, "me") as me:
            dashboard.dashboard_component(self.results_dir)
        return [c.args[0] for c in me.text.call_args_list if c.args]

    def test_a_multi_trial_run_gets_the_suite_stats(self):
        self.write("summary.csv", SUITE_SUMMARY)
        self.write(run_stats.RUN_STATS_FILE, [STABLE_PASS, FLAKY])

        texts = self.texts()

        self.assertIn("Stats across trials", texts)
        self.assertIn("56%", texts)
        self.assertNotIn("create-backup", texts)

    def test_a_single_trial_run_gets_no_stats(self):
        self.write("summary.csv", SUITE_SUMMARY[1:])

        self.assertEqual([], self.texts())


if __name__ == "__main__":
    unittest.main()
