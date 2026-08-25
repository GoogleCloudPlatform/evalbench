# Run Configuration

This YAML configuration file specifies an evaluation run on EvalBench. It outlines all the necessary components for running experiments—from specifying the dataset and database connection details to defining prompt generation, setup/teardown processes, scoring strategies, and reporting mechanisms. Below is a detailed breakdown of each section in the configuration file.

The sections below are written around NL2SQL runs, which is the default. Agentic runs use the same file with a scenario-based dataset and an agent orchestrator — see [agentic evaluations](/docs/agentic-evals.md) and the [agentic dataset format](/docs/configs/agentic-dataset-config.md).

## 1. Dataset / Evaluation Items

This section defines the primary resources used during evaluation, including the dataset containing prompts and golden SQL queries, the database configuration, and the SQL dialect used.

| **Key**           | **Required** | **Description**                                                                                                                                       |
| ----------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dataset_config`  | Yes          | Path to the JSON file that contains the list of prompts, golden SQL queries, and evaluation attributes for the run. Please see [dataset-config documentation](/docs/configs/dataset-config.md) for more info, or the [agentic dataset format](/docs/configs/agentic-dataset-config.md) for scenario-based agent runs.                                      |
| `database_configs` | Yes          | A list of paths to the YAML files that provide the database connection details. Please see [db-config documentation](/docs/configs/db-config.md) for more info. You can include multiple database_configs (i.e. one for sqlite, one for mysql) to run evals in parallel.                                                                                                                                                     |
| `dialects`         | Optional          | Specifies the SQL dialects (e.g., `mysql`, `postgres`, `sqlite`). This filters the dataset to the provided list. If not provided, all dialects found in the dataset_config json file will be used. Please see [db-config documentation](/docs/configs/db-config.md) for the list of currently supported dialects and please feel free to contribute additional dialects. |
| `databases`         | Optional          | Specifies the databases (e.g., `db_blog`, `california_schools`, etc.). This filters the dataset to the provided list of databases and ignores all other evals. If not provided, all databases found in the dataset_config json file will be tried. |
| `query_types`         | Optional          | Specifies the query_types (`dql`, `dml`, `dd`). This filters the dataset to the list of evals that are of the query_types provided. If not provided, all eval types (dql, dml and ddl) found in the dataset_config json file will be tried. |
| `dataset_format`      | Conditional (if needed) | Defines the dataset format, with `evalbench-standard-format` as the default. For BIRD datasets, it must be set to `bird-standard-format`. For scenario-based agent runs use `agent-format` (orchestrator `agent`) or `gemini-cli-format` (orchestrator `geminicli`); other supported values are `bird-interact-format`, `cortado-format`, and `dea-format`.|
| `num_trials`      | Optional     | Number of trials to run for each prompt. |
| `scenarios`      | Optional     | A list of specific scenario IDs to run (only applies to scenario-based agentic datasets like `gemini-cli-format` or `cortado-format`). Defaults to empty (runs all scenarios). |
| `scenario_pattern` | Optional     | A glob pattern of scenario IDs to run (only applies to scenario-based agentic datasets). Defaults to None (runs all scenarios). |
| `runners` | Optional | Dictionary configuring concurrency (`agent_runners`, default: 10) and prompt timeouts (`prompt_timeout_seconds`, e.g. `300` seconds per CLI execution turn). |
---

## 2. Prompt and Generation Modules

This section sets up the configurations for the model and prompt generator used to produce SQL queries from natural language.

| **Key**            | **Required** | **Description**                                                                                                                                                           |
| ------------------ | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model_config`     | Yes          | Path to the YAML configuration file for the model that will be used for SQL generation. Please see [model-config documentation](/docs/configs/model-config.md) for additional information on model_config configurations.                                                                                   |
| `prompt_generator` | Yes          | Identifier for the prompt generator module (e.g., `'SQLGenBasePromptGenerator'`), which is responsible for generating the necessary prompts for SQL generation. Please see and edit [generators](/evalbench/generators/prompts/__init__.py) for additional prompts.          |

## 3. Setup / Teardown Configuration (Optional for DDL Testing)

This is an optional bit helpful for automating the database setup and teardown process for evaluation. It is however required for running evaluations with DDLs. The `setup_directory` provides the path to the SQL setup/teardown files that will allow setting up a database before each evaluation run to ensure consistent data and schemas on every run for proper A/B testing. While these are only required for running evals that include DDL, they are highly recommended for any eval instance.
<br>

| **Key**           | **Required** | **Description**                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `setup_directory` | No*         | See description and requirements below. |

> *Note: This configuration is required when performing DDL evaluations but can be ommited for DQL and DML evaluations if the database is already setup.

### Requirements
The setup directory should include a subdirectory matching the specified database (e.g. `db_blog`) and each DB should have subdirectories for each dialect (e.g. `mysql`) it supports.
These directories must include the following 3 files:
 - `pre_setup.sql`: Prepares the environment (e.g., disabling checks).
 - `setup.sql`: Performs the actual setup operations.
 - `post_setup.sql`: Re-enables any checks or constraints.

The folder structured is described in detail below.

Additionally, you may optionally include a `data` subdirectory for setting up the database content from CSV files. The `data` subdirectory must include .csv files which are named after the tables in the schema for data insertion. This allows creating and maintaining one csv file that inserts and fills up databases across dialects rather than specifying insertions in setup.sql.

Here's an example of the directory structure:
```
setup_directory/
├── db_blog/
│   ├── mysql/
│   │   ├── pre_setup.sql
│   │   ├── setup.sql
│   │   ├── post_setup.sql
│   ├── postgres/
│   │   ├── pre_setup.sql
│   │   ├── setup.sql
│   │   ├── post_setup.sql
│   ├── data/
│   │   ├── table_one_data.csv
│   │   ├── table_two_data.csv
│   │   ├── table_three_data.csv
```

## 4. Scorer Related Configurations

The `scorers` section defines which scoring strategies run against each evaluation. The YAML key selects the scorer and the value is that scorer's configuration; scorers needing no options take `null` or `{}`. Scorers are additive — each one reports as its own row in CSV and BigQuery output.

```yaml
scorers:
  exact_match: null
  regexp_matcher:
    regexp_string_list: ["^SELECT"]
  llmrater:
    model_config: datasets/bat/model_configs/gemini_1.5-pro-002_model.yaml
```

**See the [scorer reference](/docs/scorers.md) for the full catalog and every configuration option.** The scorers most relevant to NL2SQL runs are:

| **Scorer Key** | **Description** |
| :--- | :--- |
| `exact_match` | Whether the generated query's execution result exactly matches the golden result. |
| `recall_match` | Precision and recall between generated and expected results, ignoring `None` and duplicates. |
| `set_match` | Execution accuracy as defined by the BIRD methodology. |
| `executable_sql` | Whether the generated query executes without error at all. |
| `returned_sql` | Whether the output contains actual SQL rather than only comments. |
| `regexp_matcher` | Whether the generated query matches supplied regex patterns. |
| `llmrater` | LLM comparison of golden and generated execution results. Requires `model_config`. |
| `python_scorer` | Delegates to an external Python script — [custom scorers](/docs/scorers.md#custom-scorers). |

Multi-trial consistency scorers (`exact_match_consistency`, `llm_consistency`) require `num_trials` greater than 1 — see [multi-trial consistency scorers](/docs/scorers.md#multi-trial-consistency-scorers). For agent runs, see [agentic scorers](/docs/scorers.md#agentic-scorers).

## 5. Reporting Configurations

The `reporting` section specifies how and where the evaluation results will be reported, supporting both local CSV output and Google BigQuery integration.

| **Key**    | **Required** | **Description**                                                                                                                                                  |
| ---------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `truncate_execution_outputs`| Optional (defaults to 250 rows)          | This allows overriding the truncation of outputs in reporting (CSVs, BQ) to the number of rows specified. This affects the following reporting fields: `generated_result`, `golden_result`, `golden_eval_results` `eval_results`. This prevents logging incredibly large results with potentially thousands or millions of rows. *NOTE: This does not affect any logic other than reporting.* |
| `csv`      | Optional     | Configuration for CSV reporting. <br>**Subkey:** `output_directory` specifies the directory where CSV results will be saved (e.g., `'results'`).          |
| `bigquery` | Optional     | Configuration for reporting to Google BigQuery. <br>**Subkeys:** `gcp_project_id` specifies the Google Cloud Project ID for BigQuery integration (e.g., `my_cool_gcp_project`). `dataset_id` overrides the BigQuery dataset that results are written to (`<project>.<dataset_id>`), defaulting to `evalbench`. |

---
> bigquery project_id: You can globally set your GCP project_id using the environment variables `EVAL_GCP_PROJECT_ID` or identify it separately.

## Example Configuration Snippet

Below is an example snippet of how this configuration file might appear:

```yaml
############################################################
### Dataset / Eval Items
############################################################
dataset_config: datasets/bat/prompts.json
database_config: datasets/bat/db_configs/mysql.yaml
dialect: mysql

############################################################
### Prompt and Generation Modules
############################################################
model_config: datasets/bat/model_configs/gemini_2.0_pro_model.yaml
prompt_generator: 'SQLGenBasePromptGenerator'

############################################################
### Optional - Setup / Teardown related configs (Required for testing DDL)
############################################################
setup_directory: datasets/bat/setup

############################################################
### Scorer Related Configs
############################################################
scorers:
  exact_match: null
  returned_sql: null
  regexp_matcher: null
  llmrater:
    model_config: datasets/bat/model_configs/gemini_1.5-pro-002_model.yaml
  recall_match: null
  set_match: null

############################################################
### Reporting Related Configs
############################################################
reporting:
  truncate_execution_outputs: 250
  csv:
    output_directory: 'results'
  bigquery:
    gcp_project_id: my_cool_gcp_project
    dataset_id: evalbench # Optional, defaults to 'evalbench'
```
