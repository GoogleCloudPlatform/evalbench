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

Each scenario starts from a prompt and is driven forward by an **LLM-based simulated user** that follows a `conversation_plan` until the goal is met, a terminal state is detected, or `max_turns` is reached. Every turn is captured — text, tool calls, parameters, latency, and tokens — and then scored.

### Supported Agents

| Agent | Generator | Orchestrator | Docs |
|---|---|---|---|
| Gemini CLI | `gemini_cli` | `agent` / `geminicli` | [Gemini CLI guide](/docs/gemini_cli_agent_testing.md) |
| Claude Code | `claude_code` | `agent` | [Claude Code guide](/docs/claude_code_agent_testing.md) |
| Codex CLI | `codex_cli` | `agent` | [Codex CLI guide](/docs/codex_cli_agent_testing.md) |
| Antigravity (agy) CLI | `agy_cli` | `agent` | [Antigravity CLI guide](/docs/agy_cli_agent_testing.md) |
| Conversational data agents | `dataagent` | `dataagent` / `interact` | [Data agent spec](/docs/dataagent_spec.md) |

Each run is **sandboxed** — agents execute against an isolated fake home directory (e.g. `.venv/fake_home`) so evaluations never contaminate your local machine's CLI settings, and scenarios can run concurrently.

### Tool Paradigms

Agents can be evaluated against the tools they are given, regardless of how those tools are wired up:

| Paradigm | How it Works |
|---|---|
| **MCP Servers** | Remote HTTP/SSE or local stdio Model Context Protocol servers, mounted into the agent's sandbox |
| **Extensions** | GitHub-hosted plugin packages installed idempotently via the CLI |
| **Skills** | Skill packages installed through each CLI's native mechanism |
| **Fake MCP** | A deterministic local MCP stub for fast, offline, zero-cost testing of your harness and datasets |

### Agentic Scorers

| Scorer | Type | What it Measures |
|---|---|---|
| `trajectory_matcher` | Deterministic | Expected vs. actual tool calls (Jaccard by default, Levenshtein with `enforce_order: true`) |
| `goal_completion` | LLM | Whether the agent actually accomplished the conversation plan's intent |
| `behavioral_metrics` | LLM | Hallucination rate and unnecessary-clarification rate |
| `parameter_analysis` | LLM | Qualitative feedback on the arguments passed to each tool |
| `binary_rubric_scorer` | LLM | Pass/fail against your own rubric criteria |
| `turn_count` / `agent_steps` | Deterministic | Conversation turns and internal agent steps taken |
| `end_to_end_latency` / `tool_call_latency` | Deterministic | Total wall-clock latency and time spent inside tools |
| `token_consumption` / `tokens_processed` / `effective_billed_tokens` | Deterministic | Cost and context efficiency |
| `python_scorer` | Custom | Delegates to any external Python script via `uv run` — no need to fork EvalBench |

### Quick Start: Agentic Eval

Define a scenario in an evalset:

```json
{
  "scenarios": [
    {
      "id": "list-instances-01",
      "starting_prompt": "List all Cloud SQL instances in project my-evaluation-project",
      "conversation_plan": "Ensure the agent accurately calls list_instances and returns the output.",
      "expected_trajectory": ["cloud-sql__list_instances"],
      "max_turns": 4
    }
  ]
}
```

Point a run config at it, then run:

```bash
# Offline / no-cost smoke test against a fake MCP server:
export EVAL_CONFIG=datasets/gemini-cli-tools/example_run_fake_config.yaml

# Or a real agent against a real MCP server:
export EVAL_CONFIG=datasets/claude-code-tools/example_run_config.yaml

./evalbench/run.sh
```

Ready-to-run configs live under [datasets/gemini-cli-tools/](/datasets/gemini-cli-tools/), [datasets/claude-code-tools/](/datasets/claude-code-tools/), [datasets/codex-cli-tools/](/datasets/codex-cli-tools/), and [datasets/agy-cli-tools/](/datasets/agy-cli-tools/).

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

A complete guide of Evalbench's available functionality can be found in [run-config documentation](/docs/configs/run-config.md)

Please explore the repository to learn more about customizing your evaluation workflows, integrating new metrics, and leveraging the full potential of EvalBench.


---
For additional documentation, examples, and support, please refer to the [EvalBench documentation](https://github.com/GoogleCloudPlatform/evalbench). Enjoy evaluating your GenAI models!
