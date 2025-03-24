# EvalBench

EvalBench is a flexible framework designed to measure the quality of generative AI (GenAI) workflows around database specific tasks. As of now, it provides a comprehensive set of tools, and modules to evaluate models on NL2SQL tasks, including capability of running and scoring DQL, DML, and DDL queries across multiple supported databases. Its modular, plug-and-play architecture allows you to seamlessly integrate custom components while leveraging a robust evaluation pipeline, result storage, scoring strategies, and dashboarding capabilities.

---

## Getting Started &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/GoogleCloudPlatform/evalbench/docs/examples/sqlite_example.ipynb)

Follow the steps below to run EvalBench on your local VM.
> *Note*: Evalbench requires python 3.10 or higher.

### 1. Clone the Repository

Clone the EvalBench repository from GitHub:

```bash
git clone git@github.com:GoogleCloudPlatform/evalbench.git
```

### 2. Set Up a Virtual Environment

Navigate to the repository directory and create a virtual environment:

```bash
cd evalbench
python3 -m venv venv
source venv/bin/activate
```

### 3. Install Dependencies

Install the required Python dependencies:

```bash
pip install -r requirements.txt
```

### 4. Configure GCP Authentication (For Vertex AI | Gemini Examples)

If gcloud is not installed already, follow the steps in [gcloud installation guide](https://cloud.google.com/sdk/docs/install#installation_instructions).

Then, authenticate using the Google Cloud CLI:

```bash
gcloud auth application-default login
```

This step sets up the necessary credentials for accessing Vertex AI resources on your GCP project.

We can globally set our gcp_project_id using

```bash
export EVAL_GCP_PROJECT_ID=your_project_id_here
export EVAL_GCP_PROJECT_REGION=your_region_here
```

### 5. Set Your Evaluation Configuration

For a quick start, let's run NL2SQL on some sqlite DQL queries.

1. First, read through [sqlite/run_dql.yaml](/datasets/bat/db_blog/run_configs/sqlite/run_dql.yaml) and see the configuration settings we will be running.

Now, configure your evaluation by setting the `EVAL_CONFIG` environment variable. For example, to run a configuration using the `db_blog` dataset on SQLite:

```bash
export EVAL_CONFIG=datasets/bat/db_blog/run_configs/sqlite/run_dql.yaml
```

### 6. Run EvalBench

Start the evaluation process using the provided shell script:

```bash
./evalbench/run.sh
```

---

## Overview

EvalBench's architecture is built around a modular design that supports diverse evaluation needs:
- **Modular and Plug-and-Play:** Easily integrate custom scoring modules, data processors, and dashboard components.
- **Flexible Evaluation Pipeline:** Seamlessly run DQL, DML, and DDL tasks while using a consistent base pipeline.
- **Result Storage and Reporting:** Store results in various formats (e.g., CSV, BigQuery) and visualize performance with built-in dashboards.
- **Customizability:** Configure and extend EvalBench to measure the performance of GenAI workflows tailored to your specific requirements.

A complete guide of all available functionality can be found in [run-config documentation](/docs/configs/run-config.md)

Please explore the repository to learn more about customizing your evaluation workflows, integrating new metrics, and leveraging the full potential of EvalBench.

---
For additional documentation, examples, and support, please refer to the [EvalBench documentation](https://github.com/GoogleCloudPlatform/evalbench). Enjoy evaluating your GenAI models!