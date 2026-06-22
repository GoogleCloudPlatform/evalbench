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
            # Prevent checking subdirectories with depth > 3
            if depth >= 3:
                dirs[:] = []
            if depth <= 3 and 'workflow_settings.yaml' in files:
                return root
        return None

    def _get_env_var_source_and_value(self, env_var_name: str, scenario_env: dict) -> Tuple[str | None, str | None]:
        """Returns the source ('scenario', 'host', or None) and value of the env var."""
        val = scenario_env.get(env_var_name)
        if val:
            return "scenario", val

        val = os.environ.get(env_var_name)
        if val:
            return "host", val

        return None, None

    def _resolve_env_var(
        self,
        env_var_name: str,
        scenario_env: dict,
        target_env: dict,
        log_messages: list
    ) -> None:
        """Resolves an env var from scenario or host env, logs it, and updates target_env."""
        import logging
        source, val = self._get_env_var_source_and_value(env_var_name, scenario_env)

        if source in ("scenario", "host"):
            target_env[env_var_name] = val
            msg = f"[DataformScorer] got {env_var_name} from {source}: '{val}'"
        else:
            msg = f"[DataformScorer] {env_var_name} is missing from both scenario and host env."

        logging.info(msg)
        log_messages.append(msg)

    def _setup_credentials(self, project_dir: str, project_id: str, env: dict = None) -> str:
        """Sets up .df-credentials.json in the project directory."""
        if not project_id:
            raise ValueError("Active project ID is empty.")

        region_res = subprocess.run(
            ["gcloud", "config", "get-value", "compute/region"],
            capture_output=True, text=True, check=True,
            env=env
        )
        region = region_res.stdout.strip() or "US"

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
        log_messages = []
        try:
            import json
            import logging

            # Extract env from scenario
            parsed = {}
            if isinstance(generated_eval_result, dict):
                parsed = generated_eval_result
            elif isinstance(generated_eval_result, str) and generated_eval_result:
                try:
                    parsed = json.loads(generated_eval_result)
                except json.JSONDecodeError:
                    pass

            scenario = parsed.get("scenario", {})
            scenario_env = scenario.get("env", {})

            # Initialize env with host env
            env = os.environ.copy()

            # Resolve CLOUDSDK_CORE_PROJECT
            self._resolve_env_var(
                env_var_name="CLOUDSDK_CORE_PROJECT",
                scenario_env=scenario_env,
                target_env=env,
                log_messages=log_messages
            )

            # Resolve GOOGLE_CLOUD_PROJECT
            self._resolve_env_var(
                env_var_name="GOOGLE_CLOUD_PROJECT",
                scenario_env=scenario_env,
                target_env=env,
                log_messages=log_messages
            )

            # Determine project_id for credentials setup (prefer CLOUDSDK_CORE_PROJECT to match gcloud default)
            project_id = env.get("CLOUDSDK_CORE_PROJECT") or env.get("GOOGLE_CLOUD_PROJECT")

            # Enforce project_id is not None
            if not project_id:
                return 0.0, "\n".join(log_messages) + "\nError: Both CLOUDSDK_CORE_PROJECT and GOOGLE_CLOUD_PROJECT are missing. Scorer cannot proceed."

            search_root = '.'
            if parsed:
                search_root = parsed.get("fake_home") or '.'

            project_dir = self._find_project_dir(search_root)
            if project_dir is None:
                return 0.0, "\n".join(log_messages) + f"\nCould not find workflow_settings.yaml in the workspace (search root: {search_root})."

            if "run" in command:
                try:
                    self._setup_credentials(project_dir, project_id, env=env)
                except subprocess.CalledProcessError as e:
                    return 0.0, "\n".join(log_messages) + f"\nCredentials setup failed. gcloud exit code: {e.returncode}. Stderr: {e.stderr.strip()}"
                except (FileNotFoundError, ValueError) as e:
                    return 0.0, "\n".join(log_messages) + f"\nCredentials setup failed: {e}"

            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=False,
                cwd=project_dir,
                env=env
            )

            cmd_name = " ".join(command)
            if result.returncode == 0:
                return 100.0, "\n".join(log_messages) + f"\n{cmd_name.capitalize()} succeeded."
            else:
                return 0.0, "\n".join(log_messages) + f"\n{cmd_name.capitalize()} failed with exit code {result.returncode}.\nStderr: {result.stderr}"
        except Exception as e:
            return 0.0, "\n".join(log_messages) + f"\nAn error occurred while running {' '.join(command)}: {e}"


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
