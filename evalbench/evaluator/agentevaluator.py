from typing import Any, List
import datetime
import concurrent.futures
import logging

from dataset.evalgeminicliinput import EvalGeminiCliRequest
from generators.models.gemini_cli import GeminiCliGenerator
from util.config import load_yaml_config
from mp import mprunner
from work.agentgenwork import AgentGenWork


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
            work = AgentGenWork(self.generator, self.agent_version, item, job_id=job_id, metadata=metadata)
            self.agentrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.agentrunner.futures):
            item = future.result()

            if hasattr(item, "agent_results"):
                eval_outputs.extend(item.agent_results)
            if hasattr(item, "scoring_results"):
                scoring_results.extend(item.scoring_results)

        return eval_outputs, scoring_results
