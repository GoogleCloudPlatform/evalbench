import unittest
from unittest.mock import MagicMock, patch
from scorers import multi_trial_score


class TestMultiTrialScore(unittest.TestCase):

    @patch(
        "scorers.multi_trial_score.exact_match_consistency_comparator.ExactMatchConsistencyComparator"
    )
    def test_compare_exact_match(self, mock_comparator_class):
        mock_comparator = MagicMock()
        mock_comparator.name = "exact_match_consistency"
        mock_comparator.compare.return_value = (100.0, "Match")
        mock_comparator_class.return_value = mock_comparator

        experiment_config = {"scorers": {"exact_match_consistency": {}}}

        trials = [
            {
                "id": "t1",
                "generated_sql": "SELECT 1",
                "generated_result": [{"a": 1}],
            },
            {
                "id": "t2",
                "generated_sql": "SELECT 1",
                "generated_result": [{"a": 1}],
            },
        ]

        results = multi_trial_score.compare(
            prompt_id="p1",
            nl_prompt="test",
            trials=trials,
            experiment_config=experiment_config,
            global_models={},
        )

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["score"], 100.0)
        self.assertEqual(results[0]["comparator"], "exact_match_consistency")

    @patch(
        "scorers.multi_trial_score.exact_match_consistency_comparator.ExactMatchConsistencyComparator"
    )
    def test_transitivity(self, mock_comparator_class):
        mock_comparator = MagicMock()
        mock_comparator.name = "exact_match_consistency"

        def side_effect(nl_prompt, q1, r1, er1, e1, q2, r2, er2, e2):
            if q1 == "SQL0" and q2 == "SQL1":
                return 100.0, "Match 0-1"
            if q1 == "SQL1" and q2 == "SQL2":
                return 100.0, "Match 1-2"
            return 0.0, "No match"

        mock_comparator.compare.side_effect = side_effect
        mock_comparator_class.return_value = mock_comparator

        experiment_config = {"scorers": {"exact_match_consistency": {}}}

        trials = [
            {"id": "t0", "generated_sql": "SQL0", "generated_result": []},
            {"id": "t1", "generated_sql": "SQL1", "generated_result": []},
            {"id": "t2", "generated_sql": "SQL2", "generated_result": []},
        ]

        results = multi_trial_score.compare(
            prompt_id="p1",
            nl_prompt="test",
            trials=trials,
            experiment_config=experiment_config,
            global_models={},
        )

        # Pairs: (0,1), (0,2), (1,2)
        self.assertEqual(len(results), 3)

        # Find result for (0,2)
        res_0_2 = next(
            r for r in results if r["trial1_id"] == "t0" and r["trial2_id"] == "t2"
        )
        self.assertEqual(res_0_2["score"], 100.0)
        self.assertIn("Inferred", res_0_2["logs"])

    @patch(
        "scorers.multi_trial_score.exact_match_consistency_comparator.ExactMatchConsistencyComparator"
    )
    def test_transitivity_negative(self, mock_comparator_class):
        mock_comparator = MagicMock()
        mock_comparator.name = "exact_match_consistency"

        def side_effect(nl_prompt, q1, r1, er1, e1, q2, r2, er2, e2):
            if q1 == "SQL0" and q2 == "SQL1":
                return 100.0, "Match 0-1"
            if q1 == "SQL1" and q2 == "SQL2":
                return 0.0, "No match 1-2"
            return 0.0, "Fallback"

        mock_comparator.compare.side_effect = side_effect
        mock_comparator_class.return_value = mock_comparator

        experiment_config = {"scorers": {"exact_match_consistency": {}}}

        trials = [
            {"id": "t0", "generated_sql": "SQL0", "generated_result": []},
            {"id": "t1", "generated_sql": "SQL1", "generated_result": []},
            {"id": "t2", "generated_sql": "SQL2", "generated_result": []},
        ]

        results = multi_trial_score.compare(
            prompt_id="p1",
            nl_prompt="test",
            trials=trials,
            experiment_config=experiment_config,
            global_models={},
        )

        self.assertEqual(len(results), 3)
        res_0_2 = next(
            r for r in results if r["trial1_id"] == "t0" and r["trial2_id"] == "t2"
        )
        self.assertEqual(res_0_2["score"], 0.0)
        self.assertIn("Inferred", res_0_2["logs"])

    @patch(
        "scorers.multi_trial_score.exact_match_consistency_comparator.ExactMatchConsistencyComparator"
    )
    def test_iterative_inference(self, mock_comparator_class):
        mock_comparator = MagicMock()
        mock_comparator.name = "exact_match_consistency"

        def side_effect(nl_prompt, q1, r1, er1, e1, q2, r2, er2, e2):
            if q1 == "SQL0" and q2 == "SQL1":
                return 100.0, "Match 0-1"
            if q1 == "SQL1" and q2 == "SQL2":
                return 0.0, "No match 1-2"
            if q1 == "SQL2" and q2 == "SQL3":
                return 0.0, "No match 2-3"
            if q1 == "SQL0" and q2 == "SQL3":
                return 100.0, "Match 0-3"
            return 0.0, "Fallback"

        mock_comparator.compare.side_effect = side_effect
        mock_comparator_class.return_value = mock_comparator

        experiment_config = {"scorers": {"exact_match_consistency": {}}}

        trials = [
            {"id": "t0", "generated_sql": "SQL0", "generated_result": []},
            {"id": "t1", "generated_sql": "SQL1", "generated_result": []},
            {"id": "t2", "generated_sql": "SQL2", "generated_result": []},
            {"id": "t3", "generated_sql": "SQL3", "generated_result": []},
        ]

        results = multi_trial_score.compare(
            prompt_id="p1",
            nl_prompt="test",
            trials=trials,
            experiment_config=experiment_config,
            global_models={},
        )

        self.assertEqual(len(results), 6)
        res_1_3 = next(
            r for r in results if r["trial1_id"] == "t1" and r["trial2_id"] == "t3"
        )
        self.assertEqual(res_1_3["score"], 100.0)
        self.assertIn("Inferred", res_1_3["logs"])
        self.assertEqual(mock_comparator.compare.call_count, 4)

    @patch(
        "scorers.multi_trial_score.exact_match_consistency_comparator.ExactMatchConsistencyComparator"
    )
    def test_compare_with_errors(self, mock_comparator_class):
        mock_comparator = MagicMock()
        mock_comparator.name = "exact_match_consistency"
        mock_comparator.compare.return_value = (0.0, "Error in trial")
        mock_comparator_class.return_value = mock_comparator

        experiment_config = {"scorers": {"exact_match_consistency": {}}}

        trials = [
            {"id": "t1", "generated_sql": "SELECT 1", "generated_error": "error1"},
            {
                "id": "t2",
                "generated_sql": "SELECT 1",
                "sql_generator_error": "error2",
            },
            {
                "id": "t3",
                "generated_sql": "SELECT 1",
                "prompt_generator_error": "error3",
            },
        ]

        results = multi_trial_score.compare(
            prompt_id="p1",
            nl_prompt="test",
            trials=trials,
            experiment_config=experiment_config,
            global_models={},
        )

        self.assertEqual(len(results), 3)
        self.assertEqual(mock_comparator.compare.call_count, 3)

        calls = mock_comparator.compare.call_args_list

        # Call 0: t1 and t2
        args_0, _ = calls[0]
        self.assertEqual(args_0[4], "error1")
        self.assertEqual(args_0[8], "error2")

        # Call 1: t2 and t3
        args_1, _ = calls[1]
        self.assertEqual(args_1[4], "error2")
        self.assertEqual(args_1[8], "error3")

        # Call 2: t1 and t3
        args_2, _ = calls[2]
        self.assertEqual(args_2[4], "error1")
        self.assertEqual(args_2[8], "error3")


if __name__ == "__main__":
    unittest.main()
