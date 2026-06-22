import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataset.dataset import load_dataset_from_json


class TestDatasetFilter(unittest.TestCase):
    def setUp(self):
        self.test_dir = tempfile.mkdtemp()

        # 1. Create a dummy gemini-cli dataset
        self.gemini_cli_data = {
            "id": "gemini-cli-test",
            "scenarios": [
                {"id": "csql-create-01", "starting_prompt": "create database"},
                {"id": "csql-delete-01", "starting_prompt": "delete database"},
                {"id": "spanner-list-01", "starting_prompt": "list instances"},
                {"id": "spanner-create-01", "starting_prompt": "create spanner"}
            ]
        }
        self.gemini_cli_path = os.path.join(self.test_dir, "gemini_cli_dataset.json")
        with open(self.gemini_cli_path, "w") as f:
            json.dump(self.gemini_cli_data, f)

        # 2. Create a dummy cortado dataset
        self.cortado_data = {
            "scenarios": [
                {"id": "cortado-csql-01", "starting_prompt": "csql"},
                {"id": "cortado-spanner-01", "starting_prompt": "spanner"},
                {"id": "cortado-bigquery-01", "starting_prompt": "bq"}
            ]
        }
        self.cortado_path = os.path.join(self.test_dir, "cortado_dataset.json")
        with open(self.cortado_path, "w") as f:
            json.dump(self.cortado_data, f)

        # 3. Create a dummy dea dataset
        self.dea_data = {
            "scenarios": [
                {"id": "dea-csql-01", "starting_prompt": "csql"},
                {"id": "dea-spanner-01", "starting_prompt": "spanner"},
                {"id": "dea-bigquery-01", "starting_prompt": "bq"}
            ]
        }
        self.dea_path = os.path.join(self.test_dir, "dea_dataset.json")
        with open(self.dea_path, "w") as f:
            json.dump(self.dea_data, f)

    def tearDown(self):
        shutil.rmtree(self.test_dir)

    def test_gemini_cli_no_filter(self):
        config = {"dataset_format": "gemini-cli-format"}
        result = load_dataset_from_json(self.gemini_cli_path, config)
        self.assertIn("gemini-cli-format", result)
        self.assertEqual(len(result["gemini-cli-format"]), 1)

        payload = json.loads(result["gemini-cli-format"][0].payload)
        scenarios = payload["scenarios"]
        self.assertEqual(len(scenarios), 4)
        self.assertEqual(scenarios[0]["id"], "csql-create-01")

    def test_gemini_cli_filter_by_id_list(self):
        config = {
            "dataset_format": "gemini-cli-format",
            "scenarios": ["csql-create-01", "spanner-list-01"]
        }
        result = load_dataset_from_json(self.gemini_cli_path, config)
        payload = json.loads(result["gemini-cli-format"][0].payload)
        scenarios = payload["scenarios"]
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(scenarios[0]["id"], "csql-create-01")
        self.assertEqual(scenarios[1]["id"], "spanner-list-01")

    def test_gemini_cli_filter_by_pattern(self):
        config = {
            "dataset_format": "gemini-cli-format",
            "scenario_pattern": "csql-*"
        }
        result = load_dataset_from_json(self.gemini_cli_path, config)
        payload = json.loads(result["gemini-cli-format"][0].payload)
        scenarios = payload["scenarios"]
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(scenarios[0]["id"], "csql-create-01")
        self.assertEqual(scenarios[1]["id"], "csql-delete-01")

    def test_gemini_cli_filter_combined(self):
        # Combined should do intersection (or check both conditions)
        config = {
            "dataset_format": "gemini-cli-format",
            "scenarios": ["csql-create-01", "spanner-list-01"],
            "scenario_pattern": "*list*"
        }
        result = load_dataset_from_json(self.gemini_cli_path, config)
        payload = json.loads(result["gemini-cli-format"][0].payload)
        scenarios = payload["scenarios"]
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0]["id"], "spanner-list-01")

    def test_gemini_cli_filter_no_match(self):
        config = {
            "dataset_format": "gemini-cli-format",
            "scenarios": ["non-existent-id"]
        }
        result = load_dataset_from_json(self.gemini_cli_path, config)
        self.assertIn("gemini-cli-format", result)
        self.assertEqual(len(result["gemini-cli-format"]), 0)

    def test_cortado_no_filter(self):
        config = {"dataset_format": "cortado-format"}
        result = load_dataset_from_json(self.cortado_path, config)
        self.assertIn("cortado-format", result)
        self.assertEqual(len(result["cortado-format"]), 3)
        self.assertEqual(result["cortado-format"][0].id, "cortado-csql-01")

    def test_cortado_filter_by_id_list(self):
        config = {
            "dataset_format": "cortado-format",
            "scenarios": ["cortado-csql-01", "cortado-bigquery-01"]
        }
        result = load_dataset_from_json(self.cortado_path, config)
        items = result["cortado-format"]
        self.assertEqual(len(items), 2)
        self.assertEqual(items[0].id, "cortado-csql-01")
        self.assertEqual(items[1].id, "cortado-bigquery-01")

    def test_cortado_filter_by_pattern(self):
        config = {
            "dataset_format": "cortado-format",
            "scenario_pattern": "*-spanner-*"
        }
        result = load_dataset_from_json(self.cortado_path, config)
        items = result["cortado-format"]
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, "cortado-spanner-01")

    def test_scenarios_normalization_string(self):
        # scenarios provided as a comma-separated string
        config = {
            "dataset_format": "gemini-cli-format",
            "scenarios": "csql-create-01, spanner-list-01"
        }
        result = load_dataset_from_json(self.gemini_cli_path, config)
        payload = json.loads(result["gemini-cli-format"][0].payload)
        scenarios = payload["scenarios"]
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(scenarios[0]["id"], "csql-create-01")
        self.assertEqual(scenarios[1]["id"], "spanner-list-01")

    def test_dea_no_filter(self):
        config = {"dataset_format": "dea-format"}
        result = load_dataset_from_json(self.dea_path, config)
        self.assertIn("dea-format", result)
        self.assertEqual(len(result["dea-format"]), 1)
        scenarios = result["dea-format"][0].scenario["scenarios"]
        self.assertEqual(len(scenarios), 3)
        self.assertEqual(scenarios[0]["id"], "dea-csql-01")

    def test_dea_filter_by_id_list(self):
        config = {
            "dataset_format": "dea-format",
            "scenarios": ["dea-csql-01", "dea-bigquery-01"]
        }
        result = load_dataset_from_json(self.dea_path, config)
        scenarios = result["dea-format"][0].scenario["scenarios"]
        self.assertEqual(len(scenarios), 2)
        self.assertEqual(scenarios[0]["id"], "dea-csql-01")
        self.assertEqual(scenarios[1]["id"], "dea-bigquery-01")

    def test_dea_filter_by_pattern(self):
        config = {
            "dataset_format": "dea-format",
            "scenario_pattern": "*-spanner-*"
        }
        result = load_dataset_from_json(self.dea_path, config)
        scenarios = result["dea-format"][0].scenario["scenarios"]
        self.assertEqual(len(scenarios), 1)
        self.assertEqual(scenarios[0]["id"], "dea-spanner-01")

    def test_dea_filter_no_match_returns_empty(self):
        config = {
            "dataset_format": "dea-format",
            "scenarios": ["non-existent-id"]
        }
        result = load_dataset_from_json(self.dea_path, config)
        self.assertIn("dea-format", result)
        self.assertEqual(len(result["dea-format"]), 0)


if __name__ == "__main__":
    unittest.main()
