from typing import Tuple, Any
import logging
from scorers import comparator
import subprocess
import json
import os


class PythonScorer(comparator.Comparator):
    """
    A general scorer that delegates to an external Python script via `uv run`.
    """

    def __init__(self, config: dict, name: str = "python_scorer"):
        super().__init__(config)
        self.name = name
        self.script_path = config.get("script_path")
        if not self.script_path:
            raise ValueError("script_path is required for PythonScorer")

    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
        generated_error: Any,
    ) -> Tuple[float, str]:

        # Prepare input data
        input_data = {
            "nl_prompt": nl_prompt,
            "golden_query": golden_query,
            "query_type": query_type,
            "golden_execution_result": golden_execution_result,
            "golden_eval_result": golden_eval_result,
            "golden_error": golden_error,
            "generated_query": generated_query,
            "generated_execution_result": generated_execution_result,
            "generated_eval_result": generated_eval_result,
            "generated_error": generated_error,
        }

        try:
            json_input = json.dumps(input_data)
        except Exception as e:
            return 0.0, f"FAIL: Failed to serialize input to JSON: {e}"

        # Construct command
        command = ["uv", "run", "--isolated", self.script_path]

        try:
            logging.info(f"Running PythonScorer script: {self.script_path}")
            result = subprocess.run(
                command,
                input=json_input,
                capture_output=True,
                text=True,
                check=False
            )

            if result.returncode != 0:
                return 0.0, (
                    f"FAIL: Script failed with exit code {result.returncode}. "
                    f"Stderr: {result.stderr}"
                )

            stdout = result.stdout.strip()
            try:
                response = json.loads(stdout)
                score = float(response.get("score", 0.0))
                reason = response.get("reason", "No reason provided")
                return score, reason
            except json.JSONDecodeError:
                return 0.0, (
                    f"FAIL: Failed to parse JSON from script output. "
                    f"Output: {stdout}"
                )
            except Exception as e:
                return 0.0, f"FAIL: Error processing script output: {e}"

        except FileNotFoundError:
            return 0.0, (
                "FAIL: 'uv' command not found in PATH. "
                "Please ensure 'uv' is installed."
            )
        except Exception as e:
            return 0.0, f"FAIL: Exception running script: {e}"
