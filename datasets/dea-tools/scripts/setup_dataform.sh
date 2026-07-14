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

WORKSPACE_URI=$(PYTHONPATH=evalbench .venv/bin/python3 -c "
from util.dataform_workspace import DataformWorkspaceManager
manager = DataformWorkspaceManager('$PROJECT_ID', '$REGION')
uri = manager.setup_workspace('$JOB_ID', '$SCENARIO_ID', env_files_dir='$ENV_FILES_DIR')
print(uri)
")

mkdir -p "$SESSION_DIR"
echo "$WORKSPACE_URI" > "$SESSION_DIR/target_workspace.txt"
echo "Setup complete. Workspace URI saved to $SESSION_DIR/target_workspace.txt"
