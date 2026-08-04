"""Tests for score.py module and DEFAULT_SCORERS registration."""

import json
import unittest
from unittest.mock import MagicMock, patch

from scorers import score


class TestScoreModule(unittest.TestCase):

    def test_default_scorers_map_contents(self):
        """Verify that standard registered scorers are present in DEFAULT_SCORERS."""
        expected_keys = [
            "exact_match", "recall_match", "set_match", "llmrater",
            "regexp_matcher", "returned_sql", "executable_sql",
            "trajectory_matcher", "skills_trajectory", "skills_best_practices",
            "goal_completion", "behavioral_metrics", "parameter_analysis",
            "turn_count", "agent_steps", "end_to_end_latency",
            "tool_call_latency", "token_consumption", "tokens_processed",
            "effective_billed_tokens", "binary_rubric_scorer", "python_scorer",
            "dataform_compile", "dataform_run", "dataform_cloud_compile",
            "dataform_cloud_run", "dbt_compile", "dbt_run", "dataset_quality"
        ]
        self.assertGreater(len(score.DEFAULT_SCORERS), 0)
        for key in expected_keys:
            self.assertIn(key, score.DEFAULT_SCORERS, f"Key '{key}' missing from DEFAULT_SCORERS")

    @patch("generators.models.load_yaml_config")
    def test_default_scorers_map_instantiation(self, mock_load_yaml):
        """Verify get_scorer_instance correctly instantiates all registered comparators."""
        mock_load_yaml.return_value = {"generator": "gemini_cli", "model_id": "test"}
        eval_output_item = {
            "id": 1,
            "nl_prompt": "prompt",
            "golden_sql": "SELECT 1",
            "query_type": "DQL",
            "golden_result": None,
            "golden_error": None,
            "generated_sql": "SELECT 1",
            "generated_result": None,
            "generated_error": None,
            "dialects": ["sqlite"],
            "database": "db",
            "job_id": "j1",
            "eval_results": json.dumps({"scenario": {"binary_rubric": ["Criterion A", "Criterion B"]}}),
        }
        experiment_config = {"database_configs": []}
        global_models = {"lock": MagicMock(), "registered_models": {}, "rater": MagicMock(), "simulated_user": MagicMock()}

        configs = {
            "llmrater": {"model_config": "model.yaml"},
            "skills_best_practices": {"model_config": "model.yaml"},
            "goal_completion": {"model_config": "model.yaml"},
            "behavioral_metrics": {"model_config": "model.yaml"},
            "parameter_analysis": {"model_config": "model.yaml"},
            "binary_rubric_scorer": {"model_config": "model.yaml"},
            "dataset_quality": {
                "model_config": "model.yaml",
                "product_name": "test_product",
                "sub_scorers": {"trajectory_coverage": {}},
            },
            "regexp_matcher": {"regexp_string_list": [".*"]},
            "python_scorer": {"script_path": "my_script.py"},
            "dataform_compile": {"workspace_dir": "/tmp/df"},
            "dataform_run": {"workspace_dir": "/tmp/df"},
            "dataform_cloud_compile": {"gcp_project_id": "p", "gcp_region": "l", "repository_id": "r", "timeout_seconds": 60},
            "dataform_cloud_run": {"gcp_project_id": "p", "gcp_region": "l", "repository_id": "r", "timeout_seconds": 60},
            "dbt_compile": {"project_dir": "/tmp/dbt"},
            "dbt_run": {"project_dir": "/tmp/dbt"},
        }

        for scorer_name, expected_cls in score.DEFAULT_SCORERS.items():
            cfg = configs.get(scorer_name, {})
            instances = score.get_scorer_instance(
                scorer_name, cfg, experiment_config, eval_output_item, global_models
            )
            self.assertGreater(
                len(instances), 0, f"Failed to instantiate scorer '{scorer_name}'"
            )
            for inst in instances:
                self.assertIsInstance(
                    inst,
                    expected_cls,
                    f"Scorer '{scorer_name}' returned instance of {type(inst)}, expected {expected_cls}"
                )

    @patch("scorers.pythonscorer.subprocess.run")
    def test_score_compare_execution_all_scorers(self, mock_run):
        """Verify score.compare executes built-in comparators cleanly."""
        mock_res = MagicMock()
        mock_res.returncode = 0
        mock_res.stdout = '{"score": 100.0, "reason": "Passed"}'
        mock_res.stderr = ""
        mock_run.return_value = mock_res

        eval_output_item = {
            "id": 1,
            "nl_prompt": "prompt",
            "golden_sql": "SELECT 1",
            "query_type": "DQL",
            "golden_result": None,
            "golden_error": None,
            "generated_sql": "SELECT 1",
            "generated_result": None,
            "generated_error": None,
            "dialects": ["sqlite"],
            "database": "db",
            "job_id": "j1",
            "eval_results": "",
        }

        experiment_config = {
            "scorers": {
                "exact_match": {},
                "turn_count": {},
                "python_scorer": {"script_path": "rubric.py"},
            }
        }

        scoring_results = []
        score.compare(
            eval_output_item=eval_output_item,
            experiment_config=experiment_config,
            scoring_results=scoring_results,
            global_models={},
        )

        results_by_comp = {r["comparator"]: r for r in scoring_results}
        self.assertIn("exact_match", results_by_comp)
        self.assertIn("turn_count", results_by_comp)
        self.assertIn("rubric", results_by_comp)


if __name__ == "__main__":
    unittest.main()
