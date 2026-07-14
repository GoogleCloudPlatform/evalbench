import concurrent.futures
import datetime
import json
import logging
import os
import re
from typing import Any, List

from dataset.dataengineeringagentinput import EvalDeaRequest
from generators.models.gcp_data_engineering_agent import (
    DataEngineeringAgentGenerator,
)
from google.api_core import exceptions as api_exceptions
from google.cloud import storage
from mp import mprunner
from work.agentgenwork import AgentGenWork
from evaluator.simulateduser import SimulatedUser
from work.agentscorework import AgentScoreWork
from util.config import load_yaml_config
from util.dataform_workspace import DataformWorkspaceManager

_WORKSPACE_RE = re.compile(
    r"^projects/[^/]+/locations/[^/]+/repositories/([^/]+)/workspaces/([^/]+)$"
)

# Module-level logger
logger = logging.getLogger(__name__)


class DataEngineeringAgentEvaluator:
    """Evaluator designed specifically for pure conversational DEA evaluations.

    Coordinates turn-by-turn natural language dialogue between the Simulated
    User and the generator, completely bypassing all SQL execution and
    database dependencies.
    """

    generator: DataEngineeringAgentGenerator
    agentrunner: mprunner.MPRunner

    def __init__(self, config: dict[str, Any]) -> None:
        self.config = config

        # Resolve and parse model config
        model_config = config
        if (
            "model_config" in config
            and isinstance(config["model_config"], str)
        ):
            loaded_config = load_yaml_config(config["model_config"])
            model_config = loaded_config.copy()
            model_config.update(config)

        self.generator = DataEngineeringAgentGenerator(model_config)

        runner_config = self.config.get("runners", {})
        self.agent_runners = runner_config.get("agent_runners", 10)
        self.agentrunner = mprunner.MPRunner(self.agent_runners)

    def _get_session_dir(self, job_id: str) -> str:
        """Resolves the session directory path for a given job ID."""
        reporting_config = self.config.get("reporting") or {}
        csv_config = reporting_config.get("csv") or {}
        base_output_dir = csv_config.get("output_directory", "results")
        return os.path.abspath(os.path.join(base_output_dir, job_id))

    def evaluate(
        self,
        dataset: List[EvalDeaRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        """Runs the conversational scenarios in a parallel thread pool."""
        eval_outputs: List[dict[str, Any]] = []
        scoring_results: List[dict[str, Any]] = []
        logger.info("Running pure conversational DEA evaluation")

        session_dir = self._get_session_dir(job_id)
        state_file = os.path.join(session_dir, "target_workspace.txt")
        workspace_uri = ""

        repo_id = self.config.get("dataform_repository")
        ws_id = self.config.get("dataform_workspace")
        project_id = self.config.get("gcp_project_id") or self.config.get("project_id")
        region = self.config.get("gcp_region") or self.config.get("region")

        if os.path.exists(state_file):
            with open(state_file, "r", encoding="utf-8") as sf:
                workspace_uri = sf.read().strip()
            logger.info(
                "Redirecting DEA evaluation to dynamic workspace: %s",
                workspace_uri,
            )
            match = _WORKSPACE_RE.match(workspace_uri)
            if match:
                repo_id = match.group(1)
                ws_id = match.group(2)
                for item in dataset:
                    item.gcp_resource_id = workspace_uri
                    item.dataform_repository = repo_id
                    item.dataform_workspace = ws_id
            else:
                for item in dataset:
                    item.gcp_resource_id = workspace_uri
        elif repo_id and ws_id and project_id and region:
            workspace_uri = f"projects/{project_id}/locations/{region}/repositories/{repo_id}/workspaces/{ws_id}"
            logger.info(
                "Using configured static workspace: %s",
                workspace_uri,
            )
            for item in dataset:
                item.gcp_resource_id = workspace_uri
                item.dataform_repository = repo_id
                item.dataform_workspace = ws_id
        else:
            raise ValueError(
                "No workspace configured. Either provide setup/teardown scripts "
                "for dynamic sandbox, or configure static 'dataform_repository', "
                "'dataform_workspace', 'gcp_project_id', and 'gcp_region' in your run config."
            )

        self.agentrunner.futures.clear()

        metadata = {
            "dialects": self.config.get("dialects", []),
            "database": self.config.get("database", "unknown"),
            "scorers": self.config.get("scorers", {}),
        }

        try:
            for item in dataset:
                simulated_user = SimulatedUser(self.config)
                work = AgentGenWork(
                    processor=self.process_scenario,
                    eval_result=item,
                    job_id=job_id,
                    metadata=metadata,
                    simulated_user=simulated_user,
                )
                self.agentrunner.execute_work(work)

            futures = self.agentrunner.futures
            for future in concurrent.futures.as_completed(futures):
                try:
                    modified_item = future.result()
                    if hasattr(modified_item, "agent_results"):
                        eval_outputs.extend(modified_item.agent_results)
                    if hasattr(modified_item, "scoring_results"):
                        scoring_results.extend(modified_item.scoring_results)
                except Exception as e:
                    logger.exception(f"Error getting result from future: {e}")
        finally:
            self._archive_workspace_to_gcs(workspace_uri, job_id, dataset)

        return eval_outputs, scoring_results

    def process_scenario(
        self,
        scenario: dict[str, Any],
        eval_result: EvalDeaRequest,
        job_id: str,
        metadata: dict[str, Any],
        simulated_user: SimulatedUser | None = None,
    ) -> EvalDeaRequest:
        """Manages the multi-turn conversational dialogue turn-by-turn."""

        current_prompt = scenario.get("starting_prompt", "")
        max_turns = scenario.get("max_turns", 1)
        conversation_plan = scenario.get("conversation_plan", [])
        conversation_history: List[dict[str, str]] = []
        last_agent_text = ""

        for turn in range(max_turns):
            logger.info(
                "Turn %d/%d - Prompt: %s",
                turn + 1,
                max_turns,
                current_prompt
            )

            # Pass prompt to programmatic request object
            eval_result.nl_prompt = current_prompt

            agent_text = ""
            try:
                # Native API call: mutates eval_result in-place
                self.generator.generate(eval_result)
                agent_text = getattr(eval_result, "generated_nl_response", "")
            except Exception as e:
                logger.exception(
                    "A2A SDK generation failed: %s", type(e).__name__
                )
                agent_text = f"Error: {e}"

            last_agent_text = agent_text
            logger.info(
                "Turn %d/%d - Agent Reply: %s",
                turn + 1,
                max_turns,
                agent_text
            )

            this_turn_tools = getattr(
                eval_result, "this_turn_tool_details", []
            )

            tools_by_name = {}
            for t in this_turn_tools:
                name = t["name"]
                params = t["params"]
                output = t.get("output", {})
                fail = t.get("fail", 0)

                tool_data = tools_by_name.setdefault(
                    name, {"parameters": [], "outputs": [], "fail": 0}
                )
                tool_data["parameters"].append(params)
                tool_data["outputs"].append(output)
                if fail:
                    tool_data["fail"] = 1

            agent_json = {
                "response": agent_text,
                "stats": {
                    "tools": {
                        "byName": tools_by_name
                    }
                }
            }

            conversation_history.append({
                "user": current_prompt,
                "agent": json.dumps(agent_json),
            })

            # Simulated User checks conversation plan and generates next prompt
            if turn < max_turns - 1 and simulated_user:
                next_response = simulated_user.get_next_response(
                    conversation_plan, conversation_history, agent_text
                )
                if "TERMINATE" in next_response:
                    logger.info("Simulated user met the goal and terminated.")
                    break
                current_prompt = next_response
            else:
                break

        self._finalize_scenario(
            scenario,
            last_agent_text,
            conversation_history,
            eval_result,
            job_id,
            metadata,
        )
        return eval_result

    def _archive_workspace_to_gcs(
        self, workspace_uri: str, job_id: str, dataset: list
    ) -> None:
        """Archives the Dataform workspace to GCS if configured."""
        archive_config = self.config.get("dataform_workspace_gcs_archive")
        if not archive_config or not workspace_uri:
            return

        try:
            bucket_name = archive_config.get("bucket")
            prefix = archive_config.get("path_prefix", "workspaces")
            project_id = archive_config.get("gcp_project_id")
            location = archive_config.get("gcp_region")
            if not project_id:
                raise ValueError(
                    "gcp_project_id must be specified in "
                    "dataform_workspace_gcs_archive config."
                )
            if not location:
                raise ValueError(
                    "gcp_region must be specified in "
                    "dataform_workspace_gcs_archive config."
                )
            manager = DataformWorkspaceManager(project_id, location)
            zip_bytes = manager.download_and_zip(workspace_uri)
            gcs_client = storage.Client(project=project_id)

            try:
                bucket = gcs_client.get_bucket(bucket_name)
            except api_exceptions.NotFound:
                logger.info("Bucket %s not found. Creating...", bucket_name)
                bucket = gcs_client.create_bucket(
                    bucket_name, location=location
                )

            scenario_id = self.config.get("env", {}).get(
                "SCENARIO_ID", "default"
            )

            blob_path = f"{prefix}/{job_id}/{scenario_id}_debug.zip"
            blob = bucket.blob(blob_path)

            blob.upload_from_string(
                zip_bytes, content_type="application/zip"
            )

            gcs_uri = f"gs://{bucket_name}/{blob_path}"
            logger.info("Exported workspace archive to %s", gcs_uri)

            for item in dataset:
                item.gcs_debug_archive = gcs_uri
                if not getattr(item, "dataform_repository", None):
                    item.dataform_repository = f"evalbench-{job_id}"
                if not getattr(item, "dataform_workspace", None):
                    item.dataform_workspace = "default"

                # Propagate GCS archive link and workspace coordinates
                # to finalized results in-place.
                for res in getattr(item, "agent_results", []):
                    res["gcs_debug_archive"] = gcs_uri
                    res["dataform_repository"] = item.dataform_repository
                    res["dataform_workspace"] = item.dataform_workspace
        except Exception as e:
            logger.exception(f"Error archiving workspace to GCS: {e}")

    def _finalize_scenario(
        self,
        scenario: dict[str, Any],
        last_response: str,
        conversation_history: List[dict[str, str]],
        eval_result: EvalDeaRequest,
        job_id: str,
        metadata: dict[str, Any],
    ) -> None:
        """Packages conversation and invokes scoring engine."""
        eval_output_data = {
            "eval_id": scenario["id"],
            "stdout": last_response,
            "stderr": "",
            "returncode": 0 if not last_response.startswith("Error") else 1,
            "prompt_generator_error": None,
            "generated_error": None,
            "sql_generator_error": None,
            "golden_error": None,
            # Non-SQL conversational runs skip SQL evaluation
            "generated_sql": "skipped",
            "prompt": scenario["starting_prompt"],
            "conversation_history": json.dumps(conversation_history, indent=2),
            "scenario": scenario,
            "accumulated_tools": getattr(
                eval_result, "accumulated_tools", []
            ),
            "accumulated_skills": [],
            "job_id": job_id,
            "metadata": metadata,
            "dataform_repository": getattr(
                eval_result, "dataform_repository", ""
            ),
            "dataform_workspace": getattr(
                eval_result, "dataform_workspace", ""
            ),
            "gcs_debug_archive": getattr(
                eval_result, "gcs_debug_archive", ""
            ),
        }

        score_work = AgentScoreWork(
            config=self.config,
            eval_output=eval_output_data,
            scoring_results=eval_result.scoring_results,
        )
        score_work.run()
        eval_result.agent_results.append(eval_output_data)
