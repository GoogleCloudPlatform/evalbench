# cortadoevaluator.py

from typing import Any, List, Dict
import datetime
import concurrent.futures
import logging
import json

from dataset.cortadoinput import EvalCortadoRequest
from generators.models.grpc_proxy import GrpcProxyModel
from util.config import load_yaml_config
from mp import mprunner
from work.agentgenwork import AgentGenWork
from evaluator.simulateduser import SimulatedUser
from work.agentscorework import AgentScoreWork


class CortadoEvaluator:
    def __init__(self, config):
        self.config = config

        # Load model config
        model_config = config
        if "model_config" in config and isinstance(config["model_config"], str):
            loaded_config = load_yaml_config(config["model_config"])
            model_config = loaded_config.copy()
            model_config.update(config)

        generator_type = model_config.get("generator")
        if generator_type == "grpc_proxy":
            self.generator = GrpcProxyModel(model_config)
        else:
            raise ValueError(
                f"CortadoEvaluator requires 'grpc_proxy' generator, got {generator_type}")

        runner_config = self.config.get("runners", {})
        self.agent_runners = runner_config.get("agent_runners", 10)
        self.agentrunner = mprunner.MPRunner(self.agent_runners)

    def evaluate(self, dataset: List[EvalCortadoRequest], job_id: str, run_time: datetime.datetime):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        logging.info("Running Cortado gRPC evaluation")

        self.agentrunner.futures.clear()

        metadata = {
            "dialects": self.config.get("dialects", []),
            "database": self.config.get("database", "unknown"),
            "scorers": self.config.get("scorers", {}),
        }

        # Spin up threads for concurrent conversation processing
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
            try:
                # This now contains the returned object from process_scenario
                modified_item = future.result()
                if hasattr(modified_item, "agent_results"):
                    eval_outputs.extend(modified_item.agent_results)
                if hasattr(modified_item, "scoring_results"):
                    scoring_results.extend(modified_item.scoring_results)
            except Exception as e:
                logging.error(
                    f"Error getting result from future: {e}", exc_info=True)

        return eval_outputs, scoring_results

    def process_scenario(
        self, scenario: Dict[str, Any], eval_result: Any, job_id: str,
        metadata: Dict[str, Any], simulated_user: Any = None
    ) -> Any:
        """Communication between Cortado and the Simulated User."""

        current_prompt = scenario.get("starting_prompt", "")
        max_turns = scenario.get("max_turns", 1)
        conversation_plan = scenario.get("conversation_plan", [])
        conversation_history = []
        last_agent_text = ""
        last_sql_reply = ""

        # Parity tracking lists
        accumulated_tools = []
        accumulated_skills = []

        for turn in range(max_turns):
            logging.info(
                f"Turn {turn + 1}/{max_turns} - Prompt: {current_prompt}")

            # Inject the current prompt into the object
            eval_result.nl_prompt = current_prompt

            # Hand it to the gRPC Proxy (blocks until client replies)
            agent_text = ""
            try:
                self.generator.generate(eval_result)

                nl_reply = getattr(eval_result, "generated_nl_response", "")
                sql_reply = getattr(eval_result, "generated_sql", "")
                last_sql_reply = sql_reply
                agent_text = nl_reply

            except Exception as e:
                logging.error(f'gRPC generation failed: {e}', exc_info=True)
                agent_text = f"Error: {e}"
                last_sql_reply = ""

            last_agent_text = agent_text
            logging.info(
                f"Turn {turn + 1}/{max_turns} - Agent Reply to Simulated User: {agent_text}")

            # Log history
            conversation_history.append({
                "user": current_prompt,
                "agent": agent_text
            })

            # Invoke Simulated User to check plan and generate next turn
            if turn < max_turns - 1 and simulated_user:
                next_response = simulated_user.get_next_response(
                    conversation_plan, conversation_history, agent_text
                )
                if "TERMINATE" in next_response:
                    logging.info(
                        "Simulated user met the goal and terminated the conversation.")
                    break
                current_prompt = next_response
            else:
                break

        # Finalize and Score
        self._finalize_scenario(
            scenario, last_agent_text, conversation_history,
            accumulated_tools, accumulated_skills,
            eval_result, job_id, metadata,
            last_sql_reply
        )
        return eval_result

    def _finalize_scenario(
        self, scenario: Dict[str, Any], last_response: str,
        conversation_history: List[Dict[str, str]],
        accumulated_tools: List[str], accumulated_skills: List[str],
        eval_result: Any, job_id: str, metadata: Dict[str, Any],
        last_sql: str
    ):
        """Packages the conversation and sends it to the scoring engine."""

        eval_output_data = {
            "eval_id": scenario["id"],
            "stdout": last_response,  # This is the text seen by the simulated user
            "stderr": "",
            "returncode": 0 if not last_response.startswith("Error") else 1,
            "prompt_generator_error": None,
            "generated_error": None,
            "sql_generator_error": None,
            "golden_error": None,
            "generated_sql": last_sql,
            "prompt": scenario["starting_prompt"],
            "conversation_history": json.dumps(conversation_history, indent=2),
            "scenario": scenario,
            "accumulated_tools": accumulated_tools,  # Passes empty list for now
            "accumulated_skills": accumulated_skills,  # Passes empty list for now
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
