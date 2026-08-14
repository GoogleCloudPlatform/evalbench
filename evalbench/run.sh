#!/bin/bash

if [[ -z "${EVAL_CONFIG}" ]];
  then echo "EVAL_CONFIG is required";
  exit 0;
fi

# increase limit for number of open files
ulimit -n 4096

export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
export PYTHONPATH="${SCRIPT_DIR}"

if command -v uv &> /dev/null; then
  uv run --no-sync evalbench/evalbench.py --experiment_config="$EVAL_CONFIG"
else
  python evalbench/evalbench.py --experiment_config="$EVAL_CONFIG"
fi