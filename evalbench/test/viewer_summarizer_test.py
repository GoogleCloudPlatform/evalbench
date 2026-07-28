import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

# Add viewer directory and root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../viewer")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from viewer.summarizer import get_summarizer


class TestViewerSummarizer(unittest.TestCase):

    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp_dir.cleanup)

    @patch("viewer.summarizer.get_generator")
    @patch("viewer.summarizer.load_yaml_config")
    def test_get_summarizer_default_fallback(self, mock_load_yaml, mock_get_generator):
        """Verify fallback to default model_config_path when no run_config or dataset overrides exist."""
        mock_load_yaml.return_value = {
            "model_config_path": "./gemini_summarizer_model.yaml"
        }
        mock_generator = MagicMock()
        mock_get_generator.return_value = mock_generator

        generator = get_summarizer()

        mock_get_generator.assert_called_once()
        args, _ = mock_get_generator.call_args
        self.assertTrue(args[1].endswith("gemini_summarizer_model.yaml"))
        self.assertEqual(generator, mock_generator)

    @patch("viewer.summarizer.get_generator")
    @patch("viewer.summarizer.load_yaml_config")
    def test_get_summarizer_run_config_override(self, mock_load_yaml, mock_get_generator):
        """Verify run_config.yaml in results_dir takes precedence if summarizer_model_config is defined."""
        custom_config_path = os.path.join(self.temp_dir.name, "custom_model.yaml")
        with open(custom_config_path, "w") as f:
            f.write("generator: gcp_vertex_gemini\nvertex_model: gemini-2.5-pro\n")

        run_config_path = os.path.join(self.temp_dir.name, "run_config.yaml")
        with open(run_config_path, "w") as f:
            f.write(f"summarizer_model_config: {custom_config_path}\n")

        def side_effect(path):
            if os.path.abspath(path) == os.path.abspath(run_config_path):
                return {"summarizer_model_config": custom_config_path}
            return {"model_config_path": "./gemini_summarizer_model.yaml"}

        mock_load_yaml.side_effect = side_effect
        mock_generator = MagicMock()
        mock_get_generator.return_value = mock_generator

        generator = get_summarizer(results_dir=self.temp_dir.name)

        mock_get_generator.assert_called_once()
        args, _ = mock_get_generator.call_args
        self.assertEqual(args[1], os.path.abspath(custom_config_path))

    @patch("viewer.summarizer.get_generator")
    @patch("viewer.summarizer.load_yaml_config")
    def test_get_summarizer_run_config_missing_override_falls_back(self, mock_load_yaml, mock_get_generator):
        """Verify fallback to default when run_config.yaml exists but has no summarizer_model_config."""
        run_config_path = os.path.join(self.temp_dir.name, "run_config.yaml")
        with open(run_config_path, "w") as f:
            f.write("model_config: datasets/model_configs/gemini_cli_model.yaml\n")

        def side_effect(path):
            if os.path.abspath(path) == os.path.abspath(run_config_path):
                return {"model_config": "datasets/model_configs/gemini_cli_model.yaml"}
            return {"model_config_path": "./gemini_summarizer_model.yaml"}

        mock_load_yaml.side_effect = side_effect
        mock_generator = MagicMock()
        mock_get_generator.return_value = mock_generator

        generator = get_summarizer(results_dir=self.temp_dir.name)

        mock_get_generator.assert_called_once()
        args, _ = mock_get_generator.call_args
        self.assertTrue(args[1].endswith("gemini_summarizer_model.yaml"))

    @patch("viewer.summarizer.get_generator")
    @patch("viewer.summarizer.load_yaml_config")
    def test_get_summarizer_dataset_models_mapping(self, mock_load_yaml, mock_get_generator):
        """Verify dataset_models mapping in summarizer_config.yaml matches dataset_name."""
        custom_config_path = os.path.join(self.temp_dir.name, "custom_dataset_model.yaml")
        with open(custom_config_path, "w") as f:
            f.write("generator: gcp_vertex_gemini\n")

        mock_load_yaml.return_value = {
            "model_config_path": "./gemini_summarizer_model.yaml",
            "dataset_models": {
                "agy-cli-tools": custom_config_path
            }
        }
        mock_generator = MagicMock()
        mock_get_generator.return_value = mock_generator

        generator = get_summarizer(dataset_name="agy-cli-tools")

        mock_get_generator.assert_called_once()
        args, _ = mock_get_generator.call_args
        self.assertEqual(args[1], os.path.abspath(custom_config_path))

    @patch("viewer.summarizer.get_generator")
    @patch("viewer.summarizer.load_yaml_config")
    def test_get_summarizer_explicit_model_config_override(self, mock_load_yaml, mock_get_generator):
        """Verify passing model_config_path explicitly overrides default and dataset settings."""
        custom_config_path = os.path.join(self.temp_dir.name, "override_model.yaml")
        with open(custom_config_path, "w") as f:
            f.write("generator: gcp_vertex_gemini\n")

        mock_generator = MagicMock()
        mock_get_generator.return_value = mock_generator

        generator = get_summarizer(model_config_path=custom_config_path)

        mock_get_generator.assert_called_once()
        args, _ = mock_get_generator.call_args
        self.assertEqual(args[1], os.path.abspath(custom_config_path))


if __name__ == "__main__":
    unittest.main()
