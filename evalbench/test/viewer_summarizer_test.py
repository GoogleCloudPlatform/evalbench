import os
import sys
import tempfile
import unittest
from unittest.mock import patch, MagicMock

import pandas as pd

# Add viewer directory and root to sys.path
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../../viewer")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from viewer.summarizer import (
    SCORES_MAX_CHARS,
    SCORES_MAX_COLWIDTH,
    SCORES_MAX_ROWS,
    get_summarizer,
    render_scores_for_prompt,
    summarize_eval_scoring,
)


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

        get_summarizer(results_dir=self.temp_dir.name)

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

        get_summarizer(results_dir=self.temp_dir.name)

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

        get_summarizer(dataset_name="agy-cli-tools")

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

        get_summarizer(model_config_path=custom_config_path)

        mock_get_generator.assert_called_once()
        args, _ = mock_get_generator.call_args
        self.assertEqual(args[1], os.path.abspath(custom_config_path))


class ScoresPromptBoundsTest(unittest.TestCase):
    """The scores.csv render must stay small whatever shape the run has.

    Gemini rejects prompts over 1,048,576 tokens. Production was exceeding that
    continuously because the full render was pasted into the prompt.
    """

    def test_one_huge_cell_does_not_drag_every_row_up_with_it(self):
        # The production shape that broke this: eight rows, one of which carries
        # a comparison_logs blob the size of the whole file. to_string() pads
        # each column to its widest value, so the other seven rows were each
        # billed for the blob's width in whitespace.
        blob = "x" * 1_200_000
        df = pd.DataFrame({
            "comparator": ["goal_completion"] * 8,
            "comparison_logs": [blob] + ["{}"] * 7,
            "score": [100.0] * 8,
        })

        rendered = render_scores_for_prompt(df)

        # Unbounded this is ~9.6 MB; the blob must be paid for at most once.
        self.assertLess(len(rendered), 8 * SCORES_MAX_COLWIDTH * len(df.columns))
        self.assertLess(len(rendered), len(blob))

    def test_the_columns_a_reader_needs_survive_truncation(self):
        df = pd.DataFrame({
            "comparator": ["goal_completion", "trajectory_matcher"],
            "comparison_logs": ["y" * 900_000, "{}"],
            "score": [100.0, 0.0],
        })

        rendered = render_scores_for_prompt(df)

        # Truncating the blob must not cost the narrow columns beside it, which
        # are the ones analyzer.md actually scores against.
        self.assertIn("goal_completion", rendered)
        self.assertIn("trajectory_matcher", rendered)
        self.assertIn("100.0", rendered)

    def test_a_small_scores_file_is_rendered_whole(self):
        df = pd.DataFrame({
            "comparator": ["executable", "exact_match"],
            "comparison_logs": ["ran clean", "mismatched on col 3"],
            "score": [100.0, 0.0],
        })

        rendered = render_scores_for_prompt(df)

        # Nothing here is near a bound, so nothing should be elided.
        self.assertIn("ran clean", rendered)
        self.assertIn("mismatched on col 3", rendered)
        self.assertNotIn("omitted", rendered)
        self.assertNotIn("truncated", rendered)

    def test_rows_past_the_cap_are_reported_rather_than_silently_dropped(self):
        df = pd.DataFrame({
            "comparator": ["executable"] * (SCORES_MAX_ROWS + 25),
            "score": [100.0] * (SCORES_MAX_ROWS + 25),
        })

        rendered = render_scores_for_prompt(df)

        # A summary built on a subset must say so, or it reads as complete.
        self.assertIn("25 further rows omitted", rendered)

    def test_many_wide_rows_still_land_under_the_backstop(self):
        # Neither bound alone catches this: every cell is under the width cap
        # and the row count is at the limit, yet the product is megabytes.
        df = pd.DataFrame({
            f"col_{i}": ["z" * 600] * SCORES_MAX_ROWS for i in range(10)
        })

        rendered = render_scores_for_prompt(df)

        self.assertLessEqual(len(rendered), SCORES_MAX_CHARS + len("\n... truncated ..."))
        self.assertTrue(rendered.endswith("... truncated ..."))

    @patch("viewer.summarizer.get_summarizer")
    def test_the_prompt_sent_to_gemini_is_the_bounded_render(self, mock_get_summarizer):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        pd.DataFrame({"question": ["q1"], "id": [1]}).to_csv(
            os.path.join(temp_dir.name, "evals.csv"), index=False
        )
        pd.DataFrame({
            "comparator": ["goal_completion"] * 8,
            "comparison_logs": ["x" * 1_200_000] + ["{}"] * 7,
        }).to_csv(os.path.join(temp_dir.name, "scores.csv"), index=False)

        generator = MagicMock()
        generator.vertex_model = "gemini-2.5-flash"
        mock_get_summarizer.return_value = generator
        generator.client.models.generate_content.return_value.text = "General Score: 90"

        env = {k: v for k, v in os.environ.items() if k != "GOOGLE_API_KEY"}
        with patch.dict(os.environ, env, clear=True):
            result = summarize_eval_scoring(temp_dir.name)

        self.assertEqual(result, "General Score: 90")
        _, kwargs = generator.client.models.generate_content.call_args
        prompt = kwargs["contents"]
        # ~1,048,576 tokens is the hard API limit; four chars per token is the
        # usual rough conversion, so stay an order of magnitude clear of it.
        self.assertLess(len(prompt), 400_000)
        self.assertIn("### Scores Data:", prompt)


if __name__ == "__main__":
    unittest.main()
