"""The stats across trials on the Dashboard tab of the viewer."""

import os
import sys
import tempfile
import unittest
from unittest.mock import MagicMock, patch

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
            "Stability", "4 scenarios, 3 trials each",
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
