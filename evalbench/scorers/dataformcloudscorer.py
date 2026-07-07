"""Cloud Dataform Scorers using Google Cloud Dataform API."""

import time
from typing import Tuple, List, Any
from google.cloud import dataform_v1beta1
import google.auth
from google.api_core import exceptions as api_exceptions
from scorers import comparator
import logging

logger = logging.getLogger(__name__)


class DataformCloudBaseScorer(comparator.Comparator):
    """Base class for Cloud Dataform scorers using Dataform API."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.config = config

        project = config.get("gcp_project_id")
        location = config.get("gcp_region")

        if not project:
            raise ValueError(
                "Configuration key 'gcp_project_id' is required for "
                "DataformCloudBaseScorer."
            )
        if not location:
            raise ValueError(
                "Configuration key 'gcp_region' is required for "
                "DataformCloudBaseScorer."
            )

        self.repo_name: str | None = None
        self.workspace_name: str | None = None
        self.timeout_seconds = int(config.get("timeout_seconds"))

        # Setup client
        self.credentials, _ = google.auth.default()
        self.client = dataform_v1beta1.DataformClient(
            credentials=self.credentials
        )

    def _run_workflow_invocation(
        self, compilation_result_name: str
    ) -> Tuple[Any, str, list[str]]:
        """Triggers a workflow invocation and polls for its completion."""
        invocation = self.client.create_workflow_invocation(
            request={
                "parent": self.repo_name,
                "workflow_invocation": {
                    "compilation_result": compilation_result_name
                }
            }
        )
        invocation_name = invocation.name

        start_time = time.time()
        state = invocation.state

        while state in [
            dataform_v1beta1.WorkflowInvocation.State.RUNNING,
            dataform_v1beta1.WorkflowInvocation.State.CANCELING,
        ]:
            if time.time() - start_time > self.timeout_seconds:
                raise TimeoutError(
                    f"Workflow invocation timed out after "
                    f"{self.timeout_seconds} seconds."
                )
            time.sleep(5)
            invocation = self.client.get_workflow_invocation(
                name=invocation_name
            )
            state = invocation.state

        failed_actions = []
        if state != dataform_v1beta1.WorkflowInvocation.State.SUCCEEDED:
            ActionState = dataform_v1beta1.WorkflowInvocationAction.State
            actions = self.client.query_workflow_invocation_actions(
                request={"name": invocation_name}
            )
            for act in actions:
                if act.state == ActionState.FAILED:
                    failed_actions.append(
                        f"{act.canonical_target.name}: "
                        f"{act.failure_reason}"
                    )

        return state, invocation_name, failed_actions

    def run_dataform_cloud_command(
        self, command: List[str],
        generated_eval_result: str | dict | None = None,
    ) -> Tuple[float, str]:
        """Executes a Dataform cloud command.

        Returns a score and analysis.
        """
        if generated_eval_result:
            try:
                import json
                if isinstance(generated_eval_result, dict):
                    eval_data = generated_eval_result
                else:
                    eval_data = (
                        json.loads(generated_eval_result)
                        if generated_eval_result
                        else {}
                    )
                repository_id = eval_data.get("dataform_repository")
                workspace_id = eval_data.get("dataform_workspace")
                if repository_id and repository_id != "N/A":
                    project = self.config.get("gcp_project_id")
                    location = self.config.get("gcp_region")
                    self.repo_name = (
                        f"projects/{project}/locations/{location}/"
                        f"repositories/{repository_id}"
                    )
                    self.workspace_name = (
                        f"{self.repo_name}/workspaces/{workspace_id}"
                    )
            except (json.JSONDecodeError, TypeError, AttributeError) as e:
                logger.exception(
                    "Failed to parse workspace id from generated_eval_result"
                )
                pass

        if not self.repo_name or not self.workspace_name:
            raise ValueError(
                "Dynamic repository and workspace details are required for "
                "DataformCloudBaseScorer. Make sure they are passed in "
                "generated_eval_result."
            )

        try:
            # NOTE: When both compile and run scorers are enabled,
            # each scorer independently compiles in the cloud.
            # This is a known redundancy in API billing spend.

            # Trigger compilation in the cloud (always required for both compile and run)
            compilation_result = self.client.create_compilation_result(
                request={
                    "parent": self.repo_name,
                    "compilation_result": {
                        "workspace": self.workspace_name
                    }
                }
            )

            # Check for compilation errors
            if compilation_result.compilation_errors:
                if "run" in command:
                    # Generic error for run if compilation failed
                    return 0.0, "Cannot run: cloud compilation failed."
                else:
                    # Detailed errors for compilation scorer
                    err_msgs = [
                        f"{err.message} in path {err.path}"
                        for err in compilation_result.compilation_errors
                    ]
                    return 0.0, (
                        "Cloud compilation failed with errors:\n"
                        + "\n".join(err_msgs)
                    )

            # Execute the appropriate cloud action (trigger run or use compilation result)
            if "run" in command:
                state, invocation_name, failed_actions = (
                    self._run_workflow_invocation(compilation_result.name)
                )
            else:
                state, invocation_name, failed_actions = (
                    None, compilation_result.name, []
                )

            # Evaluate the execution result
            if "run" in command:
                InvocationState = dataform_v1beta1.WorkflowInvocation.State
                if state == InvocationState.SUCCEEDED:
                    score, msg = 100.0, (
                        f"Cloud workflow run succeeded (Invocation: "
                        f"{invocation_name})."
                    )
                else:
                    err_msg = (
                        f"Cloud workflow run failed with state "
                        f"{state.name}."
                    )
                    if failed_actions:
                        err_msg += (
                            "\nFailed Actions:\n"
                            + "\n".join(failed_actions)
                        )
                    score, msg = 0.0, err_msg
            else:
                score, msg = 100.0, (
                    f"Cloud compilation succeeded (Result: "
                    f"{invocation_name})."
                )

            return score, msg

        except api_exceptions.InvalidArgument as e:
            if (
                "run" in command
                and "At least one action must be selected for execution"
                in str(e)
            ):
                return 100.0, (
                    "Cloud workflow run succeeded (empty workspace, "
                    "nothing to execute)."
                )
            raise


class DataformCloudCompileScorer(DataformCloudBaseScorer):
    """Scorer that compiles a Dataform workspace in the cloud via API."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "dataform_cloud_compile"

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
        return self.run_dataform_cloud_command(
            ["dataform", "compile"], generated_eval_result
        )


class DataformCloudRunScorer(DataformCloudBaseScorer):
    """Scorer that triggers a run in the cloud and waits for completion."""

    def __init__(self, config: dict):
        super().__init__(config)
        self.name = "dataform_cloud_run"

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
        return self.run_dataform_cloud_command(
            ["dataform", "run"], generated_eval_result
        )
