from typing import Any, List
import datetime
import concurrent.futures
import logging

from dataset.evalgeminicliinput import EvalGeminiCliRequest
from generators.models.gemini_cli import GeminiCliGenerator
from util.config import load_yaml_config
from mp import mprunner
from work.agentgenwork import AgentGenWork
from evaluator.simulateduser import SimulatedUser
from work.agentscorework import AgentScoreWork
import json
import subprocess
from typing import Dict


class AgentEvaluator:
    def __init__(
        self,
        config,
    ):
        self.config = config

        # Load model config if provided
        model_config = config
        if "model_config" in config and isinstance(config["model_config"], str):
            loaded_config = load_yaml_config(config["model_config"])
            # Merge main config into loaded config, giving precedence to main config
            model_config = loaded_config.copy()
            model_config.update(config)

        self.agent_version = model_config.get("gemini_cli_version", config.get("gemini_cli_version"))

        generator_type = model_config.get("generator")
        if generator_type == "gemini_cli":
            self.generator = GeminiCliGenerator(model_config)
        else:
            raise ValueError(f"Unsupported generator type for AgentEvaluator: {generator_type}")

        runner_config = self.config.get("runners", {})
        self.agent_runners = runner_config.get("agent_runners", 10)
        self.agentrunner = mprunner.MPRunner(self.agent_runners)

    def evaluate(
        self,
        dataset: List[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        if isinstance(self.generator, GeminiCliGenerator):
            return self._evaluate_gemini_cli(dataset, job_id, run_time)
        else:
            raise NotImplementedError("This evaluator currently only supports GeminiCliGenerator")

    def _evaluate_gemini_cli(
        self,
        dataset: List[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        logging.info("Running Gemini CLI evaluation")

        self.agentrunner.futures.clear()

        # Extract generic metadata
        metadata = {
            "dialects": self.config.get("dialects", []),
            "database": self.config.get("database", "unknown"),
            "scorers": self.config.get("scorers", {}),
        }

        for item in dataset:
            simulated_user = SimulatedUser(self.config)
            work = AgentGenWork(
                processor=self.process_scenario,
                eval_result=item,
                job_id=job_id,
                metadata=metadata,
                simulated_user=simulated_user
            )
            self.agentrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.agentrunner.futures):
            item = future.result()

            if hasattr(item, "agent_results"):
                eval_outputs.extend(item.agent_results)
            if hasattr(item, "scoring_results"):
                scoring_results.extend(item.scoring_results)

        return eval_outputs, scoring_results

    def process_scenario(
        self,
        scenario: Dict[str, Any],
        eval_result: Any,
        job_id: str,
        metadata: Dict[str, Any],
        simulated_user: Any = None
    ):
        """Processes a single scenario."""
        current_prompt = scenario["starting_prompt"]
        env = scenario.get("env", {})
        max_turns = scenario.get("max_turns", 1)
        conversation_plan = scenario.get("conversation_plan", "")
        conversation_history = []
        accumulated_tools = []
        last_result = None

        for turn in range(max_turns):
            logging.info(f"Turn {turn + 1}/{max_turns} - Prompt: {current_prompt}")

            if isinstance(self.generator, GeminiCliGenerator):
                cli_cmd = self.generator.create_command(
                    cli=self.agent_version,
                    prompt=current_prompt,
                    env=env,
                    resume=(turn > 0)
                )
                result = self.generator.safe_generate(cli_cmd)
            else:
                result = self.generator.generate(current_prompt)

            last_result = result

            self._log_cli_result(turn, max_turns, result)

            tools = []
            if isinstance(self.generator, GeminiCliGenerator):
                tools = self.generator.extract_tools(result.stdout)
            accumulated_tools.extend(tools)

            conversation_history.append({
                "user": current_prompt,
                "agent": result.stdout
            })

            if turn < max_turns - 1:
                if simulated_user:
                    next_response = simulated_user.get_next_response(
                        conversation_plan,
                        conversation_history,
                        result.stdout
                    )
                    if "TERMINATE" in next_response:
                        logging.info("Simulated user terminated conversation.")
                        break
                    current_prompt = next_response
                else:
                    break

        if last_result:
            self._finalize_scenario(
                scenario,
                last_result,
                conversation_history,
                accumulated_tools,
                eval_result,
                job_id,
                metadata
            )

    def _log_cli_result(self, turn: int, max_turns: int, result: subprocess.CompletedProcess):
        logging.info(f"Turn {turn + 1}/{max_turns} - Gemini CLI exit code: {result.returncode}")
        logging.info(f"Turn {turn + 1}/{max_turns} - Gemini CLI stdout: {result.stdout}")
        logging.info(f"Turn {turn + 1}/{max_turns} - Gemini CLI stderr: {result.stderr}")

    def _finalize_scenario(
        self,
        scenario: Dict[str, Any],
        last_result: subprocess.CompletedProcess,
        conversation_history: List[Dict[str, str]],
        accumulated_tools: List[str],
        eval_result: Any,
        job_id: str,
        metadata: Dict[str, Any]
    ):
        """Finalizes the scenario by scoring and appending results."""
        # Prepare intermediate eval_output with all necessary data for scoring
        eval_output_data = {
            "eval_id": scenario["id"],
            "stdout": last_result.stdout,
            "stderr": last_result.stderr,
            "returncode": last_result.returncode,
            "prompt_generator_error": None,
            "generated_error": None,
            "sql_generator_error": None,
            "golden_error": None,
            "generated_sql": "skipped",
            "prompt": scenario["starting_prompt"],
            "conversation_history": json.dumps(conversation_history, indent=2),
            "scenario": scenario,
            "accumulated_tools": accumulated_tools,
            "job_id": job_id,
            "metadata": metadata
        }

        score_work = AgentScoreWork(
            config=metadata,
            eval_output=eval_output_data,
            scoring_results=eval_result.scoring_results
        )
        score_work.run()

        eval_result.agent_results.append(eval_output_data)
