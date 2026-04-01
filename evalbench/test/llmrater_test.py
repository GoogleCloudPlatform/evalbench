import unittest
from scorers.llmrater import LLMRater


class TestLLMRater(unittest.TestCase):
    def test_take_n_uniques_with_document_model(self):
        # A typical Document model returned result containing nested lists of dictionaries
        golden = [
            {"authors": [{"name": "Alice"}, {"name": "Bob"}]}
        ]
        try:
            result = LLMRater.take_n_uniques(golden, 50)
            self.assertEqual(len(result), 1)
        except TypeError as e:
            self.fail(f"take_n_uniques raised TypeError unexpectedly: {e}")

    def test_take_n_uniques_with_flat_dict(self):
        # Classic SQL row model where results are flat dicts
        golden = [
            {"id": 1, "name": "Alice"},
            {"id": 2, "name": "Bob"},
            {"id": 1, "name": "Alice"}  # Duplicate should be removed
        ]
        result = LLMRater.take_n_uniques(golden, 50)
        self.assertEqual(len(result), 2)

    def test_take_n_uniques_limit(self):
        # Ensure it respects the 'n' limit
        golden = [{"id": i} for i in range(100)]
        result = LLMRater.take_n_uniques(golden, 50)
        self.assertEqual(len(result), 50)

    def test_take_n_uniques_empty(self):
        # Edge case: empty list
        result = LLMRater.take_n_uniques([], 50)
        self.assertEqual(len(result), 0)


if __name__ == '__main__':
    unittest.main()
