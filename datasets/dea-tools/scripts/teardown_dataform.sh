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
# setup_dataform.sh names the repository after this job ID.
JOB_ID=$(basename "$SESSION_DIR")

# Pass the values as environment variables. Do not put them in the Python
# source: the state file is in the session directory, so a client can write it.
WORKSPACE_URI="$WORKSPACE_URI" PROJECT_ID="$PROJECT_ID" REGION="$REGION" \
JOB_ID="$JOB_ID" PYTHONPATH=evalbench .venv/bin/python3 -c '
import os
from util.dataform_workspace import DataformWorkspaceManager
manager = DataformWorkspaceManager(os.environ["PROJECT_ID"], os.environ["REGION"])
manager.teardown_workspace(os.environ["WORKSPACE_URI"], os.environ["JOB_ID"])
'

rm -f "$STATE_FILE"
echo "Teardown complete. Deleted cloud resources for $WORKSPACE_URI"
