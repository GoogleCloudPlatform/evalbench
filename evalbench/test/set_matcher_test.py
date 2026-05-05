import unittest
from scorers.setmatcher import SetMatcher


class TestSetMatcher(unittest.TestCase):

    # --- Classic SQL Set Cases (Backwards Compatible) ---

    def test_sql_flat_match(self):
        matcher = SetMatcher({})
        golden = [{"a": 1, "b": 2}]
        generated = [{"a": 1, "b": 2}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 100)
        self.assertIsNone(err)

    def test_sql_ignore_duplicates(self):
        """Classic SQL removes duplicate rows."""
        matcher = SetMatcher({})
        golden = [{"a": 1}, {"a": 1}]
        generated = [{"a": 1}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 100)
        self.assertIsNone(err)

    def test_sql_ignore_keys(self):
        """Classic SQL compares values only."""
        matcher = SetMatcher({})
        golden = [{"a": 1}]
        generated = [{"b": 1}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 100)
        self.assertIsNone(err)

    # --- Document / NoSQL Cases (Auto-detected) ---

    def test_doc_nested_dict_match(self):
        matcher = SetMatcher({})
        golden = [{"a": {"x": 1}}]
        generated = [{"a": {"x": 1}}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 100)
        self.assertIsNone(err)

    def test_doc_multiset_duplication(self):
        """Document evaluation respects duplicate document counts."""
        matcher = SetMatcher({})
        golden = [{"a": {"x": 1}}, {"a": {"x": 1}}]
        generated = [{"a": {"x": 1}}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 0)  # Should fail if counts don't match for docs
        self.assertIsNone(err)

    def test_doc_nested_list_preserve_order(self):
        matcher = SetMatcher({})
        golden = [{"a": [1, 2]}]
        generated = [{"a": [2, 1]}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 0)  # Order inside lists matters for docs

    def test_doc_nested_list_match(self):
        matcher = SetMatcher({})
        golden = [{"a": [1, 2]}]
        generated = [{"a": [1, 2]}]
        score, err = matcher.compare(None, None, None, golden, None, None, None, generated, None, None)
        self.assertEqual(score, 100)


if __name__ == '__main__':
    unittest.main()
