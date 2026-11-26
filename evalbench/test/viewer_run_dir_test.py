"""Tests for the run directory names that the viewer gets from the browser."""

import os
import sys
import tempfile
import unittest
from unittest.mock import patch

# Add viewer directory and root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../viewer")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

import ai_comparer
import run_index


class RunDirTestCase(unittest.TestCase):

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.results = os.path.join(temp_dir.name, "results")
        self.run_dir = os.path.join(self.results, "run-1")
        self.outside = os.path.join(temp_dir.name, "outside")
        os.makedirs(self.run_dir)
        os.makedirs(self.outside)


class ResolveRunDirTest(RunDirTestCase):

    def test_accepts_a_run_directory(self):
        self.assertEqual(
            run_index.resolve_run_dir(self.results, "run-1"), self.run_dir)

    def test_rejects_names_that_are_not_one_path_component(self):
        names = [
            "", ".", "..", "../outside", "run-1/../../outside", "run-1/",
            "/", self.outside, "run-1\x00", None, 5, ["run-1"],
        ]
        for name in names:
            self.assertIsNone(
                run_index.resolve_run_dir(self.results, name), repr(name))

    def test_rejects_a_symlink_that_leaves_the_results_directory(self):
        os.symlink(self.outside, os.path.join(self.results, "link"))

        self.assertIsNone(run_index.resolve_run_dir(self.results, "link"))


class CompareEvalsRunIdTest(RunDirTestCase):

    def _compare(self, id1, id2):
        with patch.dict(os.environ, {"RESULTS_DIR": self.results}):
            with patch.object(
                ai_comparer.pd, "read_csv", side_effect=RuntimeError("stop")
            ) as read_csv:
                return ai_comparer.compare_evals(id1, id2), read_csv

    def test_rejects_a_directory_outside_the_results_directory(self):
        for bad_id in (self.outside, "../outside", ".."):
            for ids in ((bad_id, "run-1"), ("run-1", bad_id)):
                result, read_csv = self._compare(*ids)

                self.assertEqual(result, "Error: Invalid evaluation run ID.")
                read_csv.assert_not_called()

    def test_reads_a_run_directory(self):
        result, read_csv = self._compare("run-1", "run-1")

        self.assertIn("Error during comparison", result)
        read_csv.assert_called_once_with(
            os.path.join(self.run_dir, "evals.csv"))


if __name__ == "__main__":
    unittest.main()
