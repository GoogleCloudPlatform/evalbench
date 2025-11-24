#!/bin/bash
export PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python
export PYTHONPATH=evalbench:evalbench/evalproto
export _ENV_VAR_MULTIPLEXED=false
python3 evalbench/test.py