import unittest

from dataset.evalinteractinput import EvalInteractInputRequest, breakdown_datasets


def _make(id_, query_type, database, dialects):
    return EvalInteractInputRequest(
        id=id_,
        amb_user_query="",
        query_type=query_type,
        database=database,
        dialects=dialects,
        eval_query=[],
        tags=[],
        payload={},
    )


class TestBreakdownDatasets(unittest.TestCase):

    def test_total_db_len_counts_distinct_buckets(self):
        """Regression: total_db_len must reflect distinct
        (dialect, database, query_type) buckets, not stay 0."""
        dataset = [
            _make("1", "dql", "db_a", ["sqlite"]),
            _make("2", "dql", "db_a", ["sqlite"]),  # same bucket as #1
            _make("3", "dml", "db_a", ["sqlite"]),  # new query_type -> new bucket
            _make("4", "dql", "db_b", ["sqlite"]),  # new database -> new bucket
        ]

        sub_datasets, total_dataset_len, total_db_len = breakdown_datasets(dataset)

        self.assertEqual(total_db_len, 3)
        self.assertEqual(total_dataset_len, 4)
        self.assertEqual(len(sub_datasets["sqlite"]["db_a"]["dql"]), 2)
        self.assertEqual(len(sub_datasets["sqlite"]["db_a"]["dml"]), 1)
        self.assertEqual(len(sub_datasets["sqlite"]["db_b"]["dql"]), 1)

    def test_multiple_dialects_create_separate_buckets(self):
        dataset = [_make("1", "dql", "db_a", ["sqlite", "postgres"])]

        sub_datasets, _, total_db_len = breakdown_datasets(dataset)

        self.assertEqual(total_db_len, 2)
        self.assertIn("sqlite", sub_datasets)
        self.assertIn("postgres", sub_datasets)

    def test_empty_dataset(self):
        sub_datasets, total_dataset_len, total_db_len = breakdown_datasets([])
        self.assertEqual(sub_datasets, {})
        self.assertEqual(total_dataset_len, 0)
        self.assertEqual(total_db_len, 0)


if __name__ == "__main__":
    unittest.main()
