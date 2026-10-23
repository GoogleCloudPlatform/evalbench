#!/bin/bash
set -e

SESSION_DIR="$1"
if [ -z "$SESSION_DIR" ]; then
    echo "Error: SESSION_DIR not provided to setup script."
    exit 1
fi

PROJECT_ID="${EVAL_GCP_PROJECT_ID}"
REGION="${EVAL_GCP_PROJECT_REGION}"

JOB_ID=$(basename "$SESSION_DIR")

SCENARIO_ID="${SCENARIO_ID:-default}"
ENV_FILES_DIR="${EVAL_DATAFORM_SETUP_ENV_FILES_DIR}"

# Pass the values as environment variables. Do not put them in the Python
# source: SCENARIO_ID and ENV_FILES_DIR can come from the run config.
WORKSPACE_URI=$(PROJECT_ID="$PROJECT_ID" REGION="$REGION" JOB_ID="$JOB_ID" \
SCENARIO_ID="$SCENARIO_ID" ENV_FILES_DIR="$ENV_FILES_DIR" \
PYTHONPATH=evalbench .venv/bin/python3 -c '
import os
from util.dataform_workspace import DataformWorkspaceManager
manager = DataformWorkspaceManager(os.environ["PROJECT_ID"], os.environ["REGION"])
uri = manager.setup_workspace(
    os.environ["JOB_ID"],
    os.environ["SCENARIO_ID"],
    env_files_dir=os.environ["ENV_FILES_DIR"],
)
print(uri)
')

mkdir -p "$SESSION_DIR"
echo "$WORKSPACE_URI" > "$SESSION_DIR/target_workspace.txt"
echo "Setup complete. Workspace URI saved to $SESSION_DIR/target_workspace.txt"
