# EvalBench

EvalBench is a flexible framework designed to measure the quality of generative AI (GenAI) workflows. It supports two broad classes of evaluation:

- **NL2SQL / database tasks** — running and scoring DQL, DML, and DDL queries across multiple supported databases (AlloyDB, BigQuery, Spanner, PostgreSQL, MySQL, SQLite, and more).
- **Agentic evaluations** — driving real coding agents and CLIs (Gemini CLI, Claude Code, Codex CLI, Antigravity CLI) through multi-turn scenarios with an LLM-based simulated user, then scoring their tool-call trajectories, goal completion, and behavior.

Its modular, plug-and-play architecture allows you to seamlessly integrate custom components while leveraging a robust evaluation pipeline, result storage, scoring strategies, and dashboarding capabilities.

---

## Getting Started &nbsp;&nbsp;&nbsp;&nbsp;&nbsp;[![Open In Colab](https://colab.research.google.com/assets/colab-badge.svg)](https://colab.research.google.com/github/GoogleCloudPlatform/evalbench/blob/main/docs/examples/sqlite_example.ipynb)

Follow the steps below to run EvalBench on your local VM:
> *Note*: Evalbench requires Python 3.10 or higher and **uv** for dependency management.

### 1. Clone the Repository

Clone the EvalBench repository from GitHub:

```bash
git clone git@github.com:GoogleCloudPlatform/evalbench.git
```

### 2. Set Up a Virtual Environment

Navigate to the repository directory and create a virtual environment using `uv`:

```bash
cd evalbench
uv venv
source .venv/bin/activate
```

### 3. Install Dependencies

Install the required Python dependencies using `uv`:

```bash
uv sync
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

1. First, read through [datasets/bat/example_run_config.yaml](/datasets/bat/example_run_config.yaml) and see the configuration settings we will be running.

Now, configure your evaluation by setting the `EVAL_CONFIG` environment variable. For example, to run a configuration using the `db_blog` dataset on SQLite:

```bash
export EVAL_CONFIG=datasets/bat/example_run_config.yaml
```

### 6. Run EvalBench

Start the evaluation process using the provided shell script:

```bash
./evalbench/run.sh
```

---

## Agentic Evaluations

Beyond single-turn NL2SQL, EvalBench evaluates **agents** — coding CLIs and data agents that reason across multiple turns, call tools, and act on their environment.

Each scenario starts from a prompt and is driven forward by an **LLM-based simulated user** that follows a `conversation_plan` until the goal is met, a terminal state is detected, or `max_turns` is reached. Every turn is captured — text, tool calls, parameters, latency, and tokens — and then scored. Runs are **sandboxed**, so agents never touch your local CLI settings and scenarios can run concurrently.

| Agent | Generator | Orchestrator | Guide |
|---|---|---|---|
| Gemini CLI | `gemini_cli` | `agent` / `geminicli` | [Gemini CLI guide](/docs/gemini_cli_agent_testing.md) |
| Claude Code | `claude_code` | `agent` | [Claude Code guide](/docs/claude_code_agent_testing.md) |
| Codex CLI | `codex_cli` | `agent` | [Codex CLI guide](/docs/codex_cli_agent_testing.md) |
| Antigravity (agy) CLI | `agy_cli` | `agent` | [Antigravity CLI guide](/docs/agy_cli_agent_testing.md) |
| Conversational data agents | `dataagent` | `dataagent` / `interact` | [Data agent spec](/docs/dataagent_spec.md) |

Agents can be evaluated against tools wired up as **MCP servers**, **plugins**, **extensions**, or **skills** — or against a **fake MCP** stub for fast, offline, zero-cost testing. Plugins are installed from a git repo or local directory through each CLI's marketplace and may bundle skills, MCP servers, or both. Trajectories are scored for tool-call accuracy, goal completion, hallucination and clarification behavior, latency, and token cost; see the [scorer reference](/docs/scorers.md#agentic-scorers) for all of them.

To try it without any cloud resources or cost:

```bash
export EVAL_CONFIG=datasets/gemini-cli-tools/example_run_fake_config.yaml
./evalbench/run.sh
```

Read [Agentic evaluations](/docs/agentic-evals.md) for the execution model, sandboxing, and tool paradigms, and the [agentic dataset format](/docs/configs/agentic-dataset-config.md) for how to write scenarios.

---

## Overview

EvalBench's architecture is built around a modular design that supports diverse evaluation needs:
- **Modular and Plug-and-Play:** Easily integrate custom scoring modules, data processors, and dashboard components.
- **Flexible Evaluation Pipeline:** Seamlessly run DQL, DML, and DDL tasks while using a consistent base pipeline.
- **Single-Turn and Agentic:** Use the same pipeline, scorers, and reporting for one-shot NL2SQL generation and for multi-turn agent journeys driven by a simulated user.
- **Sandboxed Agent Execution:** Run real CLIs and MCP servers in isolated environments, in parallel, without touching your local configuration.
- **Result Storage and Reporting:** Store results in various formats (e.g., CSV, BigQuery) and visualize performance with built-in dashboards.
- **Customizability:** Configure and extend EvalBench to measure the performance of GenAI workflows tailored to your specific requirements.

Evalbench allows quickly creating experiments and A/B testing improvements (Available when BigQuery reporting mode set in run_config)

<img width="911" alt="Evalbench Reporting" src="https://github.com/user-attachments/assets/0881c43e-b359-472b-a7fd-e1fee6a9adf3" />

This includes being able to measure and quantify the specific improvements on databases or specific dialects:

<img width="911" alt="Evalbench Reporting by Databaes / Dialects" src="https://github.com/user-attachments/assets/e2172be1-045a-473d-92aa-304121843e7d" />

And allowing digging deeper into the exact details of the improvements and regressions including highlighting the changes, how they impacted the score and a LLM annotated explanation of the scoring changes if LLM rater is used.

<img width="911" alt="Evalbench Reporting by Databaes / Dialects" src="https://github.com/user-attachments/assets/861696b5-42f1-44c7-a7d0-710f7a32918f" />
<br><br>

---

## Documentation

Full reference documentation lives in [docs/](/docs/README.md).

| Doc | Contents |
|---|---|
| [Run config](/docs/configs/run-config.md) | The top-level YAML that drives an evaluation run |
| [Scorers](/docs/scorers.md) | Every available scorer and its configuration options |
| [Agentic evaluations](/docs/agentic-evals.md) | Execution model, sandboxing, and tool paradigms |
| [NL2SQL dataset format](/docs/configs/dataset-config.md) | Prompts, golden SQL, and eval queries |
| [Agentic dataset format](/docs/configs/agentic-dataset-config.md) | Scenarios, conversation plans, and expected trajectories |
| [Database config](/docs/configs/db-config.md) | Connection details and supported dialects |
| [Model config](/docs/configs/model-config.md) | Model selection and generation settings |
| [Examples](/docs/examples/) | Runnable notebooks for SQLite, Cloud SQL, and BigQuery |

Contributions are welcome — see [contributing](/docs/contributing.md). Enjoy evaluating your GenAI models!
