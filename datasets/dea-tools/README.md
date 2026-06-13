# How to run Evalbench for Data Engineering Agent (DEA)

This directory contains a sample configuration for evaluating the Data Engineering Agent (DEA) in a 100% programmatic, CLI-free, and SQL-free stateful multi-turn conversation.

## 1. Configure the Generator Model
The model config is located at `datasets/model_configs/gcp_data_engineering_agent_model.yaml`. It uses environment variables to dynamically resolve your GCP coordinates and target Dataform workspace:
*   `gcp_project_id`: Read from `EVAL_GCP_PROJECT_ID`
*   `gcp_region`: Read from `EVAL_GCP_PROJECT_REGION` (defaults to `us-west4` if unset)
*   `target_workspace`: Read from `EVAL_DEA_WORKSPACE`

## 2. Supply Your Evaluation Dataset
The dataset file is defined in `datasets/dea-tools/dea-live-conversational.evalset.json`. It defines conversational turns (such as reading table schemas and modifying schemas) along with evaluation metrics and rubrics.

## 3. Run EvalBench

To run the evaluation, make sure you are in the root directory of the `evalbench` repository, activate your virtual environment, and run `evalbench.py` with the required environment variables:

```bash
PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python \
EVAL_GCP_PROJECT_ID=<YOUR_GCP_PROJECT_ID> \
EVAL_GCP_PROJECT_REGION=<YOUR_GCP_REGION> \
EVAL_DEA_REPOSITORY=<YOUR_REPO_NAME> \
EVAL_DEA_WORKSPACE=<YOUR_WORKSPACE_NAME> \
.venv/bin/python3 evalbench/evalbench.py --experiment_config=datasets/dea-tools/example_run_config.yaml
```

### Key Environment Variables:
*   `EVAL_GCP_PROJECT_ID`: The GCP Project ID where your DEA agent is deployed.
*   `EVAL_GCP_PROJECT_REGION`: The GCP Region (e.g., `us-west4`) of the agent.
*   `EVAL_DEA_REPOSITORY`: The target Dataform repository name.
*   `EVAL_DEA_WORKSPACE`: The target Dataform workspace name.

## 4. Inspect Results
Upon completion, results will be generated under the `results/` folder:
*   `evals.csv`: Contains the full conversation history.
*   `scores.csv`: Contains LLM-Judge scores and detailed reasoning for the rubric checks.
