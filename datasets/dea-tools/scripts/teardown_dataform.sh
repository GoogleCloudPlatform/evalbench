#!/bin/bash
set -e

SESSION_DIR="$1"
if [ -z "$SESSION_DIR" ]; then
    echo "Error: SESSION_DIR not provided to teardown script."
    exit 1
fi

STATE_FILE="$SESSION_DIR/target_workspace.txt"
if [ ! -f "$STATE_FILE" ]; then
    echo "State file $STATE_FILE not found. Skipping teardown."
    exit 0
fi

WORKSPACE_URI=$(cat "$STATE_FILE")
PROJECT_ID="${EVAL_GCP_PROJECT_ID}"
REGION="${EVAL_GCP_PROJECT_REGION}"

PYTHONPATH=evalbench .venv/bin/python3 -c "
from util.dataform_workspace import DataformWorkspaceManager
manager = DataformWorkspaceManager('$PROJECT_ID', '$REGION')
manager.teardown_workspace('$WORKSPACE_URI')
"

rm -f "$STATE_FILE"
echo "Teardown complete. Deleted cloud resources for $WORKSPACE_URI"
