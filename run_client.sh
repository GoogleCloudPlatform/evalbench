SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" &> /dev/null && pwd)"
export PYTHONPATH="${SCRIPT_DIR}/evalbench"
python3 evalbench/client/eval_client.py --experiment="$EVAL_CONFIG" --endpoint="local"
