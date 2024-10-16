#!/bin/bash
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
export PYTHONPATH=./evalproto:.

if [[ "$TYPE" == "desktop" ]]; then
  echo "Running on desktop"
else
  echo "Running on GCP"
  /gcompute-tools/git-cookie-authdaemon
fi
cd evalbench
python3 ./eval_server.py 

