# NL2SQL Run Configuration

This YAML configuration file allows for specifying your NL2SQL evaluation run on Evalbench. It outlines all the necessary components for running experiments—from specifying the dataset and database connection details to defining prompt generation, setup/teardown processes, scoring strategies, and reporting mechanisms. Below is a detailed breakdown of each section in the configuration file.

---

## 1. Dataset / Evaluation Items

This section defines the primary resources used during evaluation, including the dataset containing prompts and golden SQL queries, the database configuration, and the SQL dialect used.

| **Key**           | **Required** | **Description**                                                                                                                                       |
| ----------------- | ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------- |
| `dataset_config`  | Yes          | Path to the JSON file that contains the list of prompts, golden SQL queries, and evaluation attributes for the run. Please see [dataset-config documentation](/docs/configs/dataset-config.md) for more info.                                      |
| `database_config` | Yes          | Path to the YAML file that provides the database connection details. Please see [db-config documentation](/docs/configs/db-config.md) for more info.                                                                                                                                                     |
| `dialect`         | Yes          | Specifies the SQL dialect to be used (e.g., `mysql`, `postgres`, `sqlite`). This filters the dataset configuration to match the supported dialect in the database configuration guide. Please see [db-config documentation](/docs/configs/db-config.md) for the list of currently supported dialects and please feel free to contribute additional dialects. |

---

## 2. Prompt and Generation Modules

This section sets up the configurations for the model and prompt generator used to produce SQL queries from natural language.

| **Key**            | **Required** | **Description**                                                                                                                                                           |
| ------------------ | ------------ | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model_config`     | Yes          | Path to the YAML configuration file for the model that will be used for SQL generation. Please see [model-config documentation](/docs/configs/model-config.md) for additional information on model_config configurations.                                                                                   |
| `prompt_generator` | Yes          | Identifier for the prompt generator module (e.g., `'MySQL_DBSchemaGenerator'`), which is responsible for generating the necessary prompts for SQL generation. Please see and edit [generators](/evalbench/generators/prompts/__init__.py) for additional prompts.          |

---

## 3. Setup / Teardown Configuration (Optional for DDL Testing)

The `setup_directory` provides the path to the SQL setup/teardown files, which are essential for running Data Definition Language (DDL) tests. The setup directory should include a subdirectory matching the specified dialect (e.g., `mysql`). Additionally, you may include a `data` subdirectory for setting up the database content from CSV files.

| **Key**           | **Required** | **Description**                                                                                                                                                                                                                                                                                                                                                                                                       |
| ----------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `setup_directory` | No*         | The directory containing SQL files for setting up and tearing down a database instance for DDL testing. This directory must have a subdirectory corresponding to the dialect (e.g., `parent_dir/my_setup_path/mysql`) that includes: <br>- `pre_setup.sql`: Prepares the environment (e.g., disabling checks). <br>- `setup.sql`: Performs the actual setup operations. <br>- `post_setup.sql`: Re-enables any checks or constraints. |

> *Hint*: You may have a `data` subdirectory which can be included with CSV files (named after the table) for data insertion. This allows creating and maintaining one csv file that inserts and fills up databases across dialects rather than specifying insertions in setup.sql.

> *Note: This configuration is required when performing DDL evaluations but can be ommited for DQL and DML evaluations.

---

## 4. Scorer Related Configurations

The `scorers` section defines various scoring strategies to evaluate the quality of the generated SQL queries. Each scorer applies a different metric or comparison strategy.

| **Scorer Key**    | **Required** | **Description**                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                               |
| ----------------- | ------------ | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `exact_match`     | Optional     | Evaluates whether the generated SQL query exactly matches the expected (golden) query.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `returned_sql`    | Optional     | Checks that the generated output contains valid SQL code rather than just comments.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| `regexp_matcher`  | Optional     | Uses regular expressions to determine if the generated query satisfies specific patterns. <br><br>**Run Configuration Options:**<br>- `regexp_string_list` (required): A list of regex patterns to match against the generated query.<br>- `invert_results` (Optional, default: `False`): When set to true, non-matching queries score 100 and matching queries score 0.<br>- `match_all_patterns` (Optional, default: `False`): If true, a score of 100 is given only if all regex patterns are matched; otherwise, a match with at least one pattern suffices.<br>- `match_whole_query` (Optional, default: `False`): When true, forces the pattern to match the entire query rather than a substring. |
| `llmrater`        | Optional     | Compares the execution results of the golden SQL query with those produced by the model. It scores 100 for concrete positive cases, such as mismatches in column names or extra columns in the generated SQL. This scorer requires its own `model_config` for proper operation.                                                                                                                                                                                                                                                                                                                                                                                                            |
| `recall_match`    | Optional     | Computes the precision and recall by comparing the generated and expected results, ignoring `None` and duplicate values. The default scoring mode is based on recall, where matching results are compared against the expected outputs regardless of their order.                                                                                                                                                                                                                                                                                                                                                                                                                        |
| `set_match`       | Optional     | Measures the execution accuracy by comparing the results of the golden query execution with those of the generated query, as defined by the BIRD methodology.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                         |

---

## 5. Reporting Configurations

This section specifies how and where the evaluation results will be reported, supporting both local CSV output and Google BigQuery integration.

| **Key**    | **Required** | **Description**                                                                                                                                                  |
| ---------- | ------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `reporting`| Yes          | Contains reporting configurations for storing evaluation results.                                                                                              |
| `csv`      | Optional     | Configuration for CSV reporting. <br>**Subkey:** `output_directory` specifies the directory where CSV results will be saved (e.g., `'results'`).          |
| `bigquery` | Optional     | Configuration for reporting to Google BigQuery. <br>**Subkey:** `gcp_project_id` specifies the Google Cloud Project ID for BigQuery integration (e.g., `my_cool_gcp_project`). |

---

## Example Configuration Snippet

Below is an example snippet of how this configuration file might appear:

```yaml
############################################################
### Dataset / Eval Items
############################################################
dataset_config: datasets/bat/db_blog/prompts/ddl.json
database_config: datasets/bat/db_blog/db_configs/mysql_db_config.yaml
dialect: mysql

############################################################
### Prompt and Generation Modules
############################################################
model_config: datasets/bat/db_blog/model_configs/gemini_2.0_pro_model.yaml
prompt_generator: 'MySQL_DBSchemaGenerator'

############################################################
### Optional - Setup / Teardown related configs (Required for testing DDL)
############################################################
setup_directory: datasets/bat/db_blog/setup

############################################################
### Scorer Related Configs
############################################################
scorers:
  exact_match: null
  returned_sql: null
  regexp_matcher: null
  llmrater:
    model_config: datasets/bat/db_blog/model_configs/gemini_1.5-pro-002_model.yaml
  recall_match: null
  set_match: null

############################################################
### Reporting Related Configs
############################################################
reporting:
  csv:
    output_directory: 'results'
  bigquery:
    gcp_project_id: my_cool_gcp_project
```
