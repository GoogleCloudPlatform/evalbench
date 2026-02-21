#!/bin/bash
set -e

# Disable Spanner OpenTelemetry metrics to prevent exit crashes
export GOOGLE_CLOUD_DISABLE_OPENTELEMETRY="true"
# Force unbuffered output for Python
export PYTHONUNBUFFERED=1
# GCP Config
export EVAL_GCP_PROJECT_ID=your-project-id
export EVAL_GCP_PROJECT_REGION=us-central1

source .venv/bin/activate

echo "======================================================="
echo "STARTING FULL BENCHMARK SUITE (Air Travel, BAT, BIRD, BIAS)"
echo "======================================================="

echo "-------------------------------------------------------"
echo "[1/6] Running Air Travel Evaluation..."
echo "-------------------------------------------------------"
python3 evalbench/evalbench.py --experiment_config datasets/air_travel/full_eval_config.yaml

echo ""
echo "-------------------------------------------------------"
echo "[2/6] Running BAT Evaluation..."
echo "-------------------------------------------------------"
python3 evalbench/evalbench.py --experiment_config datasets/bat/full_eval_config.yaml

echo ""
echo "-------------------------------------------------------"
echo "[3/6] Running BIRD Evaluation (Full 11 DBs)..."
echo "-------------------------------------------------------"
python3 evalbench/evalbench.py --experiment_config datasets/bird/full_eval_multi_engine.yaml

echo ""
echo "-------------------------------------------------------"
echo "[4/6] Running BIAS (Credit) Evaluation..."
echo "-------------------------------------------------------"
python3 evalbench/evalbench.py --experiment_config datasets/bias/eval_credit.yaml

echo ""
echo "-------------------------------------------------------"
echo "[5/6] Running BIAS (HR) Evaluation..."
echo "-------------------------------------------------------"
python3 evalbench/evalbench.py --experiment_config datasets/bias/eval_hr.yaml

echo ""
echo "-------------------------------------------------------"
echo "[6/6] Running BIAS (Medical) Evaluation..."
echo "-------------------------------------------------------"
python3 evalbench/evalbench.py --experiment_config datasets/bias/eval_medical.yaml

echo "======================================================="
echo "FULL BENCHMARK COMPLETE"
echo "======================================================="
