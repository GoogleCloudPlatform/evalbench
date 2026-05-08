"""dbt Scorers

This module provides scorers for dbt projects, including compilation and execution.
"""

import os
import shutil
import subprocess
from typing import Any, List, Tuple

from scorers import comparator


class DbtBaseScorer(comparator.Comparator):
    """Base class for dbt scorers."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.config = config

    def _find_project_dir(self, search_root: str = ".") -> str | None:
        """Finds the project directory containing dbt_project.yml."""
        for root, dirs, files in os.walk(search_root):
            # Limit search to depth 3 from search_root
            depth = root.count(os.sep) - search_root.count(os.sep)
            if depth >= 3:
                dirs[:] = []  # Stop recursion
            if depth <= 3 and "dbt_project.yml" in files:
                return root
        return None

    def _has_profiles_yml(self, project_dir: str) -> bool:
        """Checks if profiles.yml exists in the project directory."""
        return os.path.exists(os.path.join(project_dir, "profiles.yml"))

    def _check_dbt(self) -> bool:
        """Checks if the dbt CLI is available."""
        return shutil.which("dbt") is not None

    def run_dbt_command(
        self, command: List[str], generated_eval_result: Any = None
    ) -> Tuple[float, str]:
        """Executes a dbt command and returns a score and analysis."""
        try:
            import json

            search_root = "."
            if isinstance(generated_eval_result, dict):
                search_root = generated_eval_result.get("fake_home") or "."
            elif isinstance(generated_eval_result, str) and generated_eval_result:
                try:
                    parsed = json.loads(generated_eval_result)
                    search_root = parsed.get("fake_home") or "."
                except json.JSONDecodeError:
                    # Fallback to '.' if the generated_eval_result is not valid JSON.
                    pass

            project_dir = self._find_project_dir(search_root)
            if project_dir is None:
                return (
                    0.0,
                    f"Could not find dbt_project.yml in the workspace (search root: {search_root}).",
                )

            if not self._check_dbt():
                return 0.0, "dbt CLI not setup, unable to run the scorer."

            # Build the command with project and profiles directories
            full_command = list(command)
            full_command.extend(["--project-dir", project_dir])

            # If profiles.yml is in the project dir, use it
            if self._has_profiles_yml(project_dir):
                full_command.extend(["--profiles-dir", project_dir])

            try:
                result = subprocess.run(
                    full_command,
                    capture_output=True,
                    text=True,
                    check=False,
                    cwd=project_dir,
                    timeout=300,
                )
            except subprocess.TimeoutExpired:
                cmd_str = " ".join(full_command)
                return 0.0, f"FAIL: '{cmd_str}' timed out after 300 seconds."

            cmd_str = " ".join(full_command)
            if result.returncode == 0:
                return 100.0, f"'{cmd_str}' succeeded."
            else:
                return (
                    0.0,
                    f"'{cmd_str}' failed with exit code {result.returncode}.\nStderr: {result.stderr}\nStdout: {result.stdout}",
                )
        except Exception as e:
            return 0.0, f"An error occurred while running dbt: {e}"


class DbtCompileScorer(DbtBaseScorer):
    """Scorer that validates if a dbt project compiles successfully."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "dbt_compile"

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: list,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: list,
        generated_eval_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        return self.run_dbt_command(
            ["dbt", "compile"], generated_eval_result
        )


class DbtRunScorer(DbtBaseScorer):
    """Scorer that validates if a dbt project runs successfully."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "dbt_run"

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: list,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: list,
        generated_eval_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        return self.run_dbt_command(["dbt", "run"], generated_eval_result)
