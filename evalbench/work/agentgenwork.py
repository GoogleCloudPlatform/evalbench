"""AgentGenWork class."""

from typing import Any, List
import json
import logging
import subprocess

from work.work import Work
from generators.models.gemini_cli import GeminiCliGenerator, CLICommand
from scorers.trajectorymatcher import TrajectoryMatcher


class AgentGenWork(Work):
    """Work class for running agent generation and scoring."""

    def __init__(
        self,
        generator: GeminiCliGenerator,
        agent_version: str,
        eval_result: Any,
        job_id: str = "",
        metadata: dict = None
    ):
        self.generator = generator
        self.agent_version = agent_version
        self.eval_result = eval_result
        self.job_id = job_id
        self.metadata = metadata or {}

    def run(self, work_config: Any = None) -> Any:
        """Runs the agent generation and scoring work.

        Args:
            work_config: Optional configuration for the work.

        Returns:
            The updated eval_result with results and scores.
        """
        eval_result = self.eval_result
        eval_outputs = []
        scoring_results = []

        # Initialize results structure in eval_result if not present
        if not hasattr(eval_result, "agent_results"):
            eval_result.agent_results = []
        if not hasattr(eval_result, "scoring_results"):
            eval_result.scoring_results = []

        try:
            eval_set = json.loads(eval_result.payload)
            for scenario in eval_set.get("scenarios", []):
                prompt = scenario["starting_prompt"]
                env = scenario.get("env", {})

                cli_cmd = CLICommand(
                    cli=self.agent_version,
                    prompt=prompt,
                    env=env,
                )
                result = self._run_gemini_cli(cli_cmd)

                logging.info(f"Gemini CLI exit code: {result.returncode}")
                logging.info(f"Gemini CLI stdout: {result.stdout}")
                logging.info(f"Gemini CLI stderr: {result.stderr}")

                score, explanation = self._score_result(result, scenario)

                eval_output_data = {
                    "eval_id": scenario["id"],
                    "stdout": result.stdout,
                    "stderr": result.stderr,
                    "returncode": result.returncode,
                    "prompt_generator_error": None,
                    "generated_error": None,
                    "sql_generator_error": None,
                    "golden_error": None,
                    "generated_sql": "skipped",
                }
                scoring_result_data = {
                    "id": scenario["id"],
                    "eval_id": scenario["id"],
                    "score": score,
                    "explanation": explanation,
                    "comparator": "trajectory_matcher",
                    "generated_sql": "skipped",
                    "generated_error": None,
                    "job_id": self.job_id,
                    "database": self.metadata.get("database", "unknown"),
                    "dialects": self.metadata.get("dialects", []),
                }
                scoring_results.append(scoring_result_data)

            # Update the eval_result with results
            eval_result.agent_results.extend(eval_outputs)
            eval_result.scoring_results.extend(scoring_results)

        except Exception as e:
            logging.error(f"Error processing item: {e}")
            # Potentially record error in eval_result

        return eval_result

    def _run_gemini_cli(self, cli_cmd: CLICommand) -> subprocess.CompletedProcess:
        result = self.generator.generate(cli_cmd)
        if isinstance(result, str) and not result:
            return subprocess.CompletedProcess(
                args=cli_cmd.cli,
                returncode=1,
                stdout="",
                stderr="Error: Generator returned empty response (possibly resource exhausted).",
            )
        return result

    def _score_result(self, result: subprocess.CompletedProcess, scenario: dict) -> tuple[int, str]:
        score = 0
        explanation = ""
        try:
            output_json = json.loads(result.stdout)
            executed_tools = []
            if (
                "stats" in output_json
                and "tools" in output_json["stats"]
                and "byName" in output_json["stats"]["tools"]
            ):
                executed_tools = list(
                    output_json["stats"]["tools"]["byName"].keys()
                )

            expected_trajectory = scenario.get("expected_trajectory", [])

            scorer_config = self.metadata.get("scorers", {}).get("trajectory_matcher", {})
            matcher = TrajectoryMatcher(scorer_config)
            score, explanation = matcher.compare(
                nl_prompt=scenario["starting_prompt"],
                golden_query="",
                query_type="",
                golden_execution_result=expected_trajectory,
                golden_eval_result="",
                golden_error="",
                generated_query="",
                generated_execution_result=executed_tools,
                generated_eval_result="",
                generated_error=None
            )

        except json.JSONDecodeError:
            score = 0
            explanation = "Failed to parse Gemini CLI output as JSON."
        except Exception as e:
            score = 0
            explanation = f"An error occurred during scoring: {e}"

        return score, explanation
