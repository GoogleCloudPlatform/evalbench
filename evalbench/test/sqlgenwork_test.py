import unittest
from unittest.mock import MagicMock
from work.sqlgenwork import SQLGenWork


class TestSQLGenWork(unittest.TestCase):

    def setUp(self):
        self.mock_generator = MagicMock()
        self.eval_result = {
            "prompt_generator_error": None,
            "generated_sql": None,
            "sql_generator_error": None,
            "generated_prompt": "prompt1",
        }

    def test_get_generated_sql_from_dict(self):
        work = SQLGenWork(self.mock_generator, self.eval_result)
        result = {"generated_sql": "SELECT * FROM t1", "extra": "info"}
        sql = work._get_generated_sql(result)
        self.assertEqual(sql, "SELECT * FROM t1")

    def test_get_generated_sql_from_string(self):
        work = SQLGenWork(self.mock_generator, self.eval_result)
        result = "SELECT * FROM t1"
        sql = work._get_generated_sql(result)
        self.assertEqual(sql, "SELECT * FROM t1")

    def test_attach_generator_info_with_dict(self):
        work = SQLGenWork(self.mock_generator, self.eval_result)
        result = {"extra": "info"}
        work._attach_generator_info(self.eval_result, result)
        self.assertEqual(self.eval_result["extra"], "info")

    def test_attach_generator_info_with_string(self):
        work = SQLGenWork(self.mock_generator, self.eval_result)
        result = "SELECT * FROM t1"
        work._attach_generator_info(self.eval_result, result)
        self.assertEqual(len(self.eval_result), 4)

    def test_run_success(self):
        self.mock_generator.name = "standard_generator"
        self.mock_generator.generate.return_value = {
            "generated_sql": "SELECT 1;", "other_meta": "xyz"}

        work = SQLGenWork(self.mock_generator, self.eval_result)
        result = work.run()

        self.assertEqual(result["generated_sql"], "SELECT 1;")
        self.assertEqual(result["other_meta"], "xyz")
        self.assertIn("sql_generator_time", result)
        self.assertIsNone(result["sql_generator_error"])

    def test_run_noop_generator(self):
        self.mock_generator.name = "noop_generator"
        self.eval_result["generated_sql"] = "SELECT NOOP;"

        work = SQLGenWork(self.mock_generator, self.eval_result)
        result = work.run()

        self.mock_generator.generate.assert_not_called()
        self.assertEqual(result["generated_sql"], "SELECT NOOP;")

    def test_run_generator_exception(self):
        self.mock_generator.name = "standard_generator"
        self.mock_generator.generate.side_effect = Exception("Test Error")

        work = SQLGenWork(self.mock_generator, self.eval_result)
        result = work.run()

        self.assertEqual(result["sql_generator_error"], "Test Error")
        self.assertIsNone(result["generated_sql"])


if __name__ == "__main__":
    unittest.main()
