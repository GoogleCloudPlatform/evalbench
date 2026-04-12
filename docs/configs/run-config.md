# NL2SQL Run Configuration

This YAML configuration file allows for specifying your NL2SQL evaluation run on Evalbench. It outlines all the necessary components for running experiments—from specifying the dataset and database connection details to defining prompt generation, setup/teardown processes, scoring strategies, and reporting mechanisms. Below is a detailed breakdown of each section in the configuration file.

## 1. Dataset / Evaluation Items

This section defines the primary resources used during evaluation, including the dataset containing prompts and golden SQL queries, the database configuration, and the SQL dialect used.

| **Key**           | **Required** | **Description**                                                                                                                                       |
| ----------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dataset_config`  | Yes          | Path to the JSON file that contains the list of prompts, golden SQL queries, and evaluation attributes for the run. Please see [dataset-config documentation](/docs/configs/dataset-config.md) for more info.                                      |
| `database_configs` | Yes          | A list of paths to the YAML files that provide the database connection details. Please see [db-config documentation](/docs/configs/db-config.md) for more info. You can include multiple database_configs (i.e. one for sqlite, one for mysql) to run evals in parallel.                                                                                                                                                     |
| `dialects`         | Optional          | Specifies the SQL dialects (e.g., `mysql`, `postgres`, `sqlite`). This filters the dataset to the provided list. If not provided, all dialects found in the dataset_config json file will be used. Please see [db-config documentation](/docs/configs/db-config.md) for the list of currently supported dialects and please feel free to contribute additional dialects. |
| `databases`         | Optional          | Specifies the databases (e.g., `db_blog`, `california_schools`, etc.). This filters the dataset to the provided list of databases and ignores all other evals. If not provided, all databases found in the dataset_config json file will be tried. |
| `query_types`         | Optional          | Specifies the query_types (`dql`, `dml`, `dd`). This filters the dataset to the list of evals that are of the query_types provided. If not provided, all eval types (dql, dml and ddl) found in the dataset_config json file will be tried. |
| `dataset_format`      | Conditional (if needed) | Defines the dataset format, with `evalbench-standard-format` as the default. For BIRD datasets, it must be set to `bird-standard-format`.|
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

The `scorers` section defines various scoring strategies to evaluate the quality of the generated SQL queries. Each scorer applies a different metric or comparison strategy.

| **Scorer Key**    | **Required** | **Description**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `exact_match`     | Optional     | Evaluates whether the generated SQL query result exactly matches the expected (golden) query result.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `returned_sql`    | Optional     | Checks that the generated output contains valid SQL code rather than just comments.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `regexp_matcher`  | Optional     | Uses regular expressions to determine if the generated query satisfies specific patterns. <br><br>**Run Configuration Options:**<br>- `regexp_string_list` (required): A list of regex patterns to match against the generated query.<br>- `invert_results` (Optional, default: `False`): When set to true, non-matching queries score 100 and matching queries score 0.<br>- `match_all_patterns` (Optional, default: `False`): If true, a score of 100 is given only if all regex patterns are matched; otherwise, a match with at least one pattern suffices.<br>- `match_whole_query` (Optional, default: `False`): When true, forces the pattern to match the entire query rather than a substring. |
| `llmrater`        | Optional     | Compares the execution results of the golden SQL query with those produced by the model. It scores 100 for concrete positive cases, such as mismatches in column names or extra columns in the generated SQL. This scorer requires its own `model_config` for proper operation.                                                                                                                                                                                                                                                                                                                                                                                                            |
| `recall_match`    | Optional     | Computes the precision and recall by comparing the generated and expected results, ignoring `None` and duplicate values. The default scoring mode is based on recall, where matching results are compared against the expected outputs regardless of their order.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `set_match`       | Optional     | Measures the execution accuracy by comparing the results of the golden query execution with those of the generated query, as defined by the BIRD methodology.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

## 5. Reporting Configurations

The `reporting` section specifies how and where the evaluation results will be reported, supporting both local CSV output and Google BigQuery integration.

| **Key**    | **Required** | **Description**                                                                                                                                                  |
| ---------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `truncate_execution_outputs`| Optional (defaults to 250 rows)          | This allows overriding the truncation of outputs in reporting (CSVs, BQ) to the number of rows specified. This affects the following reporting fields: `generated_result`, `golden_result`, `golden_eval_results` `eval_results`. This prevents logging incredibly large results with potentially thousands or millions of rows. *NOTE: This does not affect any logic other than reporting.* |
| `csv`      | Optional     | Configuration for CSV reporting. <br>**Subkey:** `output_directory` specifies the directory where CSV results will be saved (e.g., `'results'`).          |
| `bigquery` | Optional     | Configuration for reporting to Google BigQuery. <br>**Subkey:** `gcp_project_id` specifies the Google Cloud Project ID for BigQuery integration (e.g., `my_cool_gcp_project`). |

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
```
