import unittest
from scorers.exact_match_consistency_comparator import ExactMatchConsistencyComparator


class TestExactMatchConsistencyComparator(unittest.TestCase):

    def test_exact_match(self):
        comparator = ExactMatchConsistencyComparator({})
        trial1_result = [{"a": 1}]
        trial2_result = [{"a": 1}]
        score, logs = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=trial1_result,
            trial1_eval_result="",
            trial1_error="",
            trial2_query="SELECT 1",
            trial2_execution_result=trial2_result,
            trial2_eval_result="",
            trial2_error="",
        )
        self.assertEqual(score, 100.0)

    def test_mismatch(self):
        comparator = ExactMatchConsistencyComparator({})
        trial1_result = [{"a": 1}]
        trial2_result = [{"a": 2}]
        score, logs = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=trial1_result,
            trial1_eval_result="",
            trial1_error="",
            trial2_query="SELECT 2",
            trial2_execution_result=trial2_result,
            trial2_eval_result="",
            trial2_error="",
        )
        self.assertEqual(score, 0.0)

    def test_error_inconsistent(self):
        comparator = ExactMatchConsistencyComparator({})
        score, logs = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=[],
            trial1_eval_result="",
            trial1_error="error1",
            trial2_query="SELECT 1",
            trial2_execution_result=[],
            trial2_eval_result="",
            trial2_error="error2",
        )
        self.assertEqual(score, 0.0)
        self.assertIn("Inconsistent errors", logs)

    def test_error_consistent(self):
        comparator = ExactMatchConsistencyComparator({})
        score, logs = comparator.compare(
            nl_prompt="test",
            trial1_query="SELECT 1",
            trial1_execution_result=[],
            trial1_eval_result="",
            trial1_error="error",
            trial2_query="SELECT 1",
            trial2_execution_result=[],
            trial2_eval_result="",
            trial2_error="error",
        )
        self.assertEqual(score, 100.0)
        self.assertIn("Both trials failed with the same error", logs)


if __name__ == "__main__":
    unittest.main()
