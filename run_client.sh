export PYTHONPATH=./evalbench:./evalbench/evalproto
python3 evalbench/client/eval_client.py --experiment="$EVAL_CONFIG" --endpoint="local"
