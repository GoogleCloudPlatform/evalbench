#!/bin/bash
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/evalbench"

# if [[ "$TYPE" == "desktop" ]]; then
#   echo "Running on desktop"
# else
#   echo "Running on GCP"
#   /gcompute-tools/git-cookie-authdaemon
# fi
cd evalbench
python3 ./eval_server.py 

