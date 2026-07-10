# How to run Evalbench for Data Engineering Agent (DEA)

This directory contains a sample configuration for evaluating the Data Engineering Agent (DEA) in a 100% programmatic, CLI-free, and SQL-free stateful multi-turn conversation.

## 1. Supply Your Evaluation Dataset
The dataset file is defined in `datasets/dea-tools/dea-live-conversational.evalset.json`. It defines conversational turns (such as reading table schemas and modifying schemas) along with evaluation metrics and rubrics.

## 2. Run EvalBench

You can run EvalBench for DEA in two modes:

### Mode 1: Dynamic Sandbox
EvalBench automatically creates a temporary Dataform repository and workspace for the evaluation and tears them down afterwards. This ensures a clean slate for every run.

To use this mode, ensure `set_up_script` and `tear_down_script` are enabled in your run config (default behavior in `example_run_config.yaml`).

```bash
EVAL_GCP_PROJECT_ID=<YOUR_GCP_PROJECT_ID> \
EVAL_GCP_PROJECT_REGION=<YOUR_GCP_REGION> \
.venv/bin/python3 evalbench/evalbench.py --experiment_config=datasets/dea-tools/example_run_config.yaml
```

### Mode 2: Static Workspace
EvalBench uses a specific, pre-existing Dataform repository and workspace that you manage.

To use this mode:
1. In `example_run_config.yaml`, comment out `set_up_script` and `tear_down_script`.
2. Uncomment `dataform_repository` and `dataform_workspace`.
3. Provide the repository and workspace IDs in the environment:

```bash
EVAL_GCP_PROJECT_ID=<YOUR_GCP_PROJECT_ID> \
EVAL_GCP_PROJECT_REGION=<YOUR_GCP_REGION> \
EVAL_DEA_REPOSITORY_ID=<YOUR_REPO_ID> \
EVAL_DEA_WORKSPACE_ID=<YOUR_WORKSPACE_ID> \
.venv/bin/python3 evalbench/evalbench.py --experiment_config=datasets/dea-tools/example_run_config.yaml
```

> [!NOTE]
> If both the setup/teardown scripts and the static repository/workspace coordinates are configured in the YAML run config, the dynamic sandbox logic will override the static configuration.

### Key Environment Variables:
*   `EVAL_GCP_PROJECT_ID`: The GCP Project ID where your DEA agent is deployed.
*   `EVAL_GCP_PROJECT_REGION`: The GCP Region (e.g., `us-west4`) of the agent.
*   `EVAL_DEA_REPOSITORY_ID`: The target Dataform repository ID (Required for Mode 2, not needed for Mode 1).
*   `EVAL_DEA_WORKSPACE_ID`: The target Dataform workspace ID (Required for Mode 2, not needed for Mode 1).

## 3. Inspect Results
Upon completion, results will be generated in two locations:

### Local Files (under the `results/` folder):
*   `evals.csv`: Contains the full conversation history.
*   `scores.csv`: Contains LLM-Judge scores and detailed reasoning for the rubric checks.

### Google BigQuery (Cloud Database):
If enabled in `example_run_config.yaml`, results are automatically uploaded to your GCP project under the table `<YOUR_GCP_PROJECT_ID>.evalbench.results`. 

A clickable **Looker Studio Dashboard** link will be printed in the terminal console upon completion to visually inspect the conversation flows and scores.
