"""Dataform Scorers

This module provides scorers for Dataform projects, including compilation and execution.
"""

import subprocess
import os
import textwrap
from typing import Tuple, List, Any

from scorers import comparator


class DataformBaseScorer(comparator.Comparator):
    """Base class for Dataform scorers."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.config = config

    def _find_project_dir(self, search_root: str = '.') -> str | None:
        """Finds the project directory containing workflow_settings.yaml."""
        for root, dirs, files in os.walk(search_root):
            # Limit search to depth 3 from search_root
            depth = root.count(os.sep) - search_root.count(os.sep)
            if depth >= 3:
                dirs[:] = []  # Stop recursion
            if 'workflow_settings.yaml' in files:
                return root
        return None

    def _setup_credentials(self, project_dir: str) -> str:
        """Sets up .df-credentials.json in the project directory."""
        project_res = subprocess.run(
            ["gcloud", "config", "get-value", "project"],
            capture_output=True, text=True, check=True
        )
        project_id = project_res.stdout.strip()

        region_res = subprocess.run(
            ["gcloud", "config", "get-value", "compute/region"],
            capture_output=True, text=True, check=True
        )
        region = region_res.stdout.strip() or "US"

        if not project_id:
            raise ValueError("Active project ID is empty in gcloud configuration.")

        credentials_path = os.path.join(project_dir, ".df-credentials.json")
        credentials_content = textwrap.dedent(f"""\
        {{
          "projectId": "{project_id}",
          "location": "{region}"
        }}
        """)
        with open(credentials_path, "w") as f:
            f.write(credentials_content)
        return project_id

    def run_dataform_command(self, command: List[str], generated_eval_result: Any = None) -> Tuple[float, str]:
        """Executes a Dataform command and returns a score and analysis."""
        try:
            import json

            search_root = '.'
            if isinstance(generated_eval_result, dict):
                search_root = generated_eval_result.get("fake_home") or '.'
            elif isinstance(generated_eval_result, str) and generated_eval_result:
                try:
                    parsed = json.loads(generated_eval_result)
                    search_root = parsed.get("fake_home") or '.'
                except json.JSONDecodeError:
                    pass

            project_dir = self._find_project_dir(search_root)
            if project_dir is None:
                return 0.0, f"Could not find workflow_settings.yaml in the workspace (search root: {search_root})."

            if "run" in command:
                try:
                    self._setup_credentials(project_dir)
                except subprocess.CalledProcessError as e:
                    return 0.0, f"Credentials setup failed. gcloud exit code: {e.returncode}. Stderr: {e.stderr.strip()}"
                except (FileNotFoundError, ValueError) as e:
                    return 0.0, f"Credentials setup failed: {e}"

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                cwd=project_dir
            )

            cmd_name = " ".join(command)
            if result.returncode == 0:
                return 100.0, f"{cmd_name.capitalize()} succeeded."
            else:
                return 0.0, f"{cmd_name.capitalize()} failed with exit code {result.returncode}.\nStderr: {result.stderr}"
        except Exception as e:
            return 0.0, f"An error occurred while running {' '.join(command)}: {e}"


class DataformCompileScorer(DataformBaseScorer):
    """Scorer for Dataform compilation."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "dataform_compile"

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
        return self.run_dataform_command(["dataform", "compile"], generated_eval_result)


class DataformRunScorer(DataformBaseScorer):
    """Scorer for Dataform execution."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "dataform_run"

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
        return self.run_dataform_command(["dataform", "run"], generated_eval_result)
