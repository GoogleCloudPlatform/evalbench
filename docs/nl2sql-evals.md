# NL2SQL Evaluations

EvalBench evaluates **NL2SQL**: a model turns a natural-language question into SQL, the query runs against a real database, and the result is scored against a golden query. It uses the same run config, scorer framework, and reporting as [agentic evaluations](/docs/agentic-evals.md).

**Contents**

- [How it works](#how-it-works)
- [Quick start](#quick-start)
- [Query types and database setup](#query-types-and-database-setup)
- [Scoring](#scoring)
- [BigQuery experiment reports](#bigquery-experiment-reports)

---

## How it works

1. The **run config** ties together the dataset, databases, model config, prompt generator, scorers, and reporting.
2. Each **eval item** in the [dataset](/docs/configs/dataset-config.md) provides a natural-language prompt, golden SQL per dialect, and optional eval queries.
3. Items are filtered by the `dialects` and `query_types` set in the run config, and run against each configured [database](/docs/configs/db-config.md).
4. The **prompt generator** builds the model prompt, and the [model](/docs/configs/model-config.md) generates SQL.
5. Generated and golden SQL are both executed, and every configured [scorer](/docs/scorers.md#sql-scorers) compares them.
6. Results are written to CSV and/or BigQuery.

Set `num_trials` to repeat each item and measure consistency with the [multi-trial scorers](/docs/scorers.md#multi-trial-consistency-scorers).

---

## Quick start

[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/GoogleCloudPlatform/evalbench/blob/main/docs/examples/sqlite_example.ipynb)

From a cloned repo, this generates and scores DQL queries against the `db_blog` dataset on a local SQLite database:

```bash
export EVAL_CONFIG=datasets/bat/example_run_config.yaml
./evalbench/run.sh
```

See [example_run_config.yaml](/datasets/bat/example_run_config.yaml) for the dataset, database, generation, and scorer settings. More runnable notebooks, covering Cloud SQL and BigQuery, are in [docs/examples/](/docs/examples/).

---

## Query types and database setup

| Query type | What it covers | Setup required |
|---|---|---|
| **DQL** | Read queries (`SELECT`) | No, if the database is already populated |
| **DML** | Data changes (`INSERT`, `UPDATE`, `DELETE`) | Recommended |
| **DDL** | Schema changes (`CREATE`, `ALTER`, `DROP`) | Yes |

Setup and teardown reset each database to a known schema and data set before every run, so A/B comparisons stay consistent. See [setup / teardown configuration](/docs/configs/run-config.md#3-setup--teardown-configuration-optional-for-ddl-testing) for the required directory layout.

---

## Scoring

A typical NL2SQL scorer set:

```yaml
scorers:
  exact_match: null
  set_match: null
  executable_sql: null
  returned_sql: null
  llmrater:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
```

See the [SQL scorers](/docs/scorers.md#sql-scorers) reference for every option.

---

## BigQuery experiment reports

With `bigquery` reporting enabled in the [run config](/docs/configs/run-config.md#5-reporting-configurations), you can set up experiments and A/B test improvements across runs:

<img width="911" alt="Evalbench Reporting" src="https://github.com/user-attachments/assets/0881c43e-b359-472b-a7fd-e1fee6a9adf3" />

You can measure improvements on specific databases or dialects:

<img width="911" alt="Evalbench Reporting by Databases / Dialects" src="https://github.com/user-attachments/assets/e2172be1-045a-473d-92aa-304121843e7d" />

You can also drill into individual improvements and regressions: what changed, how it affected the score, and an LLM-written explanation when `llmrater` is used:

<img width="911" alt="Evalbench Reporting Details" src="https://github.com/user-attachments/assets/861696b5-42f1-44c7-a7d0-710f7a32918f" />
