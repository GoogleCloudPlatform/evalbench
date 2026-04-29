from typing import Any, List
import datetime
import concurrent.futures
import logging
import threading

from dataset.evalgeminicliinput import EvalGeminiCliRequest
from generators.models import get_generator
from generators.models.gemini_cli import GeminiCliGenerator
from generators.models.claude_code import ClaudeCodeGenerator
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

        model_config_path = config.get("model_config")
        if not isinstance(model_config_path, str):
            raise ValueError(
                "AgentEvaluator requires `model_config` to be a path to a model YAML")

        global_models = {
            "lock": threading.Lock(),
            "registered_models": {},
        }
        self.generator = get_generator(global_models, model_config_path)

        if isinstance(self.generator, ClaudeCodeGenerator):
            self.agent_version = self.generator.claude_code_version
        elif isinstance(self.generator, GeminiCliGenerator):
            self.agent_version = self.generator.gemini_cli_version
        else:
            raise ValueError(
                f"AgentEvaluator only supports gemini_cli and claude_code generators, "
                f"got {type(self.generator).__name__}")

        runner_config = self.config.get("runners", {})
        self.agent_runners = runner_config.get("agent_runners", 10)
        self.agentrunner = mprunner.MPRunner(self.agent_runners)

    def evaluate(
        self,
        dataset: List[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        if isinstance(self.generator, (GeminiCliGenerator, ClaudeCodeGenerator)):
            return self._evaluate_agent_cli(dataset, job_id, run_time)
        else:
            raise NotImplementedError(
                "This evaluator currently only supports GeminiCliGenerator and ClaudeCodeGenerator")

    def _evaluate_agent_cli(
        self,
        dataset: List[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        generator_name = type(self.generator).__name__
        logging.info(f"Running {generator_name} evaluation")

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
        accumulated_skills = []
        last_result = None

        session_id = None
        for turn in range(max_turns):
            logging.info(
                f"Turn {turn + 1}/{max_turns} - Prompt: {current_prompt}")
            if isinstance(self.generator, (GeminiCliGenerator, ClaudeCodeGenerator)):
                if isinstance(self.generator, ClaudeCodeGenerator):
                    cli_cmd = self.generator.create_command(
                        cli=self.agent_version,
                        prompt=current_prompt,
                        env=env,
                        resume=(turn > 0),
                        session_id=session_id
                    )
                else:
                    cli_cmd = self.generator.create_command(
                        cli=self.agent_version,
                        prompt=current_prompt,
                        env=env,
                        resume=(turn > 0)
                    )
                try:
                    result = self.generator.safe_generate(cli_cmd)
                    if isinstance(self.generator, ClaudeCodeGenerator) and result.stdout:
                        parsed = self.generator.parse_response(result.stdout)
                        if parsed.get("session_id"):
                            session_id = parsed["session_id"]
                except Exception as e:
                    logging.error(f'CLI execution failed: {e}')
                    result = subprocess.CompletedProcess(
                        args=[self.agent_version], returncode=1, stdout='', stderr=str(e)
                    )
            else:
                try:
                    result = self.generator.generate(current_prompt)
                except Exception as e:
                    logging.error(f'LLM generation failed: {e}')
                    result = str(e)

            last_result = result

            self._log_cli_result(turn, max_turns, result)

            tools = []
            if isinstance(self.generator, (GeminiCliGenerator, ClaudeCodeGenerator)):
                tools = self.generator.extract_tools(result.stdout)
            accumulated_tools.extend(tools)

            # Extract skills from generator output
            if isinstance(self.generator, (GeminiCliGenerator, ClaudeCodeGenerator)):
                skills = self.generator.extract_skills(result.stdout)
                accumulated_skills.extend(skills)

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
                accumulated_skills,
                eval_result,
                job_id,
                metadata
            )

    def _log_cli_result(self, turn: int, max_turns: int, result: subprocess.CompletedProcess):
        generator_name = self.generator.name
        logging.info(
            f"Turn {turn + 1}/{max_turns} - {generator_name} exit code: {result.returncode}")
        logging.info(
            f"Turn {turn + 1}/{max_turns} - {generator_name} stdout: {result.stdout}")
        logging.info(
            f"Turn {turn + 1}/{max_turns} - {generator_name} stderr: {result.stderr}")

    def _finalize_scenario(
        self,
        scenario: Dict[str, Any],
        last_result: subprocess.CompletedProcess,
        conversation_history: List[Dict[str, str]],
        accumulated_tools: List[str],
        accumulated_skills: List[str],
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
            "accumulated_skills": accumulated_skills,
            "job_id": job_id,
            "metadata": metadata
        }

        score_work = AgentScoreWork(
            config=self.config,
            eval_output=eval_output_data,
            scoring_results=eval_result.scoring_results
        )
        score_work.run()

        eval_result.agent_results.append(eval_output_data)
