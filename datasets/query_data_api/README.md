# How to run Evalbench for QueryData API Generator

This directory contains a sample configuration for evaluating a database natural language agent using the Google Cloud Gemini Data Analytics `QueryData` API generator.

## 1. Configure the Database
First, update the database configuration in `db_configs/alloydb.yaml` (or create a new YAML file for your specific database dialect). 
Replace the placeholders (e.g., `<YOUR_GCP_PROJECT_ID>`, `<YOUR_INSTANCE_ID>`) with your actual database coordinates.

## 2. Configure the Generator Model
Update `model_configs/querydata_config.yaml` to point to your specific GCP project and your Data Agent Context setup. 
Replace placeholders such as `<YOUR_CONTEXT_SET_ID>` with the actual Context Set ID you wish to evaluate against.

## 3. Supply Your Evaluation Dataset
Update `queries.json` with your evaluation test cases. You must provide:
- `nl_prompt`: The natural language question you are asking the agent.
- `golden_sql`: The correct SQL query that answers the question (used by the evaluator to score the response).

## 4. Run EvalBench

You can start the evaluation process using the standalone release binary or by running it directly from the source repository.

### Option A: Using the Release Binary
1. Download the latest release archive (`.tar.gz` or `.zip`) for your operating system from the [GitHub Releases page](https://github.com/GoogleCloudPlatform/evalbench/releases).
2. Extract the archive to obtain the standalone `evalbench` executable. *(Note: Avoid extracting it into the root of the evalbench source repository, as it will conflict with the existing `evalbench` code folder).*
3. Ensure the binary has execute permissions:
   ```bash
   chmod +x ./evalbench
   ```
4. Run the evaluation by passing the configuration file as a flag:
   ```bash
   ./evalbench --experiment_config=datasets/query_data_api/experiment_config.yaml
   ```

### Option B: Running From Source
If you are executing from the source codebase, you can pass the configuration via the `EVAL_CONFIG` environment variable and run the helper shell script:

```bash
export EVAL_CONFIG=datasets/query_data_api/experiment_config.yaml
./evalbench/run.sh
```
