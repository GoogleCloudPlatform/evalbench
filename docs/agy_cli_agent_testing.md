# Antigravity (agy) CLI Evaluation Guide

This guide covers how to use EvalBench for evaluating [Antigravity CLI](https://antigravity.google/product/antigravity-cli) (`agy`)
agent workflows using **MCP Servers** and **Skills**.

---

## Table of Contents

- [Overview](#overview)
- [Architecture](#architecture)
- [Prerequisites](#prerequisites)
- [Quick Start](#quick-start)
- [Configuration Reference](#configuration-reference)
  - [Run Configuration](#1-run-configuration)
  - [Model Configuration](#2-model-configuration)
  - [Evaluation Dataset (Evalset)](#3-evaluation-dataset-evalset)
- [Authentication & Supported Models](#authentication--supported-models)
- [Tool Paradigms](#tool-paradigms)
  - [MCP Servers](#mcp-servers)
  - [Skills](#skills)
  - [Fake MCP Servers (Testing)](#fake-mcp-servers-testing)
- [Scorers & Reporting](#scorers--reporting)
- [Comparison Reference (vs. Gemini CLI)](#comparison-reference-vs-gemini-cli)
- [Troubleshooting](#troubleshooting)

---

## Overview

EvalBench's agy CLI integration enables automated, multi-turn evaluation of
agentic AI workflows running on the Antigravity CLI binary. EvalBench drives
the agent with simulated users, captures tool invocations and token usage via
agy's structured event stream, and evaluates correctness with configurable
scorers.

### Key Capabilities

- **Multi-turn evaluation** driven by LLM-powered simulated users following
  natural language conversation plans.
- **Two tool paradigms**: Model Context Protocol (MCP) servers (HTTP and
  stdio) and Skills (packaged as plugins).
- **Fake MCP server support** for deterministic, offline testing without cloud
  resources.
- **Automated scoring and reporting** across trajectory matching, goal
  completion, CSV export, and Google BigQuery.

---

## Architecture

The evaluation pipeline coordinates test scenarios, user simulation, and the
sandboxed Antigravity CLI process:

```
Run Config -> AgentOrchestrator -> AgentEvaluator -> AgyCliGenerator -> agy
                                                       |
                                                       v
                                              MCP servers / skills
```

1. **AgentOrchestrator** (`orchestrator: agent`) loads the dataset and
   dispatches evaluation scenarios.
2. **AgentEvaluator** manages the multi-turn interaction loop between the
   generator and the **SimulatedUser** LLM.
3. **AgyCliGenerator** (`generator: agy_cli`) manages the sandboxed agy CLI
   process, stages credentials and tool configurations, executes non-interactively
   via `--output-format stream-json`, and extracts tool calls, responses, and
   token stats.

---

## Prerequisites

1. **Python 3.10+** and project dependencies installed using `uv`:
   ```bash
   cd evalbench
   uv sync
   ```

2. **GCP Authentication** (Application Default Credentials):
   ```bash
   gcloud auth application-default login
   ```

3. **Environment Variables**:
   ```bash
   export EVAL_GCP_PROJECT_ID=your_project_id
   export EVAL_GCP_PROJECT_REGION=global
   ```

> [!NOTE]
> **You do not need `agy` installed globally on the host.** EvalBench installs its
> own isolated copy into `<fake_home>/.local/bin/` per session and always runs
> that sandboxed binary.

---

## Quick Start

### 1. Set the run configuration

```bash
# For MCP Server evaluation:
export EVAL_CONFIG=datasets/agy-cli-tools/example_run_config.yaml

# For Skills evaluation:
export EVAL_CONFIG=datasets/agy-cli-tools/example_run_skills_config.yaml

# For Fake MCP (offline testing):
export EVAL_CONFIG=datasets/agy-cli-tools/example_run_fake_config.yaml
```

### 2. Run the evaluation

```bash
./evalbench/run.sh
```

---

## Configuration Reference

### 1. Run Configuration

Set `orchestrator: agent` and `dataset_format: agent-format` in your top-level
YAML configuration:

| Key | Required | Description |
|-----|----------|-------------|
| `dataset_config` | Yes | Path to the evalset JSON file |
| `dataset_format` | Yes | Must be `agent-format` (or legacy `gemini-cli-format`) |
| `orchestrator` | Yes | Must be `agent` (or legacy `geminicli`) |
| `model_config` | Yes | Path to the agy CLI model config YAML |
| `simulated_user_model_config` | Yes | Path to the model config for the simulated user LLM |
| `scorers` | Yes | Dictionary of scorer configurations |
| `reporting` | Yes | CSV and/or BigQuery output options |

**Example** ([example_run_config.yaml](../datasets/agy-cli-tools/example_run_config.yaml)):

```yaml
dataset_config: datasets/agy-cli-tools/agy-cli.evalset.json
dataset_format: agent-format
orchestrator: agent
model_config: datasets/model_configs/agy_cli_model.yaml
simulated_user_model_config: datasets/model_configs/gemini_3.1_pro_model.yaml

scorers:
  trajectory_matcher: {}
  goal_completion:
    model_config: datasets/model_configs/gemini_3.1_pro_model.yaml
  turn_count: {}

reporting:
  csv:
    output_directory: 'results'
```

---

### 2. Model Configuration

Specifies the generator, model label, execution timeouts, and environment:

| Key | Required | Description |
|-----|----------|-------------|
| `generator` | Yes | Must be `agy_cli` |
| `model` | Optional | Model label (e.g. `"Gemini 3.1 Pro (Low)"` or `"Gemini 3.5 Flash (Medium)"`). Omit to use agy's default. |
| `timeout` | Optional | CLI turn timeout string (e.g. `"20m"`, passed to `--print-timeout`). Defaults to 5m. |
| `env` | Required | Environment block containing `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION`. |
| `setup` | Optional | Tool setup block for `mcp_servers`, `skills`, or `fake_mcp_servers`. |

> [!IMPORTANT]
> **Project Configuration Required:** agy resolves its GCP backend project from
> `settings.json`, which EvalBench populates from `env.GOOGLE_CLOUD_PROJECT`.
> Always include `GOOGLE_CLOUD_PROJECT` in your model config `env` block.

---

### 3. Evaluation Dataset (Evalset)

Evalsets define the test scenarios. See the [agentic dataset format](configs/agentic-dataset-config.md)
for field definitions. Expected tool trajectories use the canonical
`<server>__<tool>` format.

Example scenario:
```json
{
  "scenarios": [
    {
      "id": "list-instances-01",
      "starting_prompt": "List all Cloud SQL instances in project cloud-db-nl2sql",
      "conversation_plan": "Ensure the agent accurately calls list_instances. Verify the output is returned correctly.",
      "expected_trajectory": ["cloud-sql__list_instances"],
      "max_turns": 4
    }
  ]
}
```

---

## Authentication & Supported Models

agy authenticates non-interactively using **Google Application Default
Credentials (ADC)** (`AGY_ADC_AUTH=true`), which also supplies outbound
credentials for `authProviderType: google_credentials` MCP servers.

### Supported Model Tiers
* **Supported under ADC**: Flash models (e.g. `"Gemini 3.5 Flash (Medium)"`,
  `"Gemini 3.6 Flash (Medium)"`) and **`"Gemini 3.1 Pro (Low)"`**.
* **Not supported under ADC**: `"Gemini 3.1 Pro (High)"` requires interactive
  OAuth user login and will fail if configured for automated ADC runs.

---

## Tool Paradigms

### MCP Servers

Configured under `setup.mcp_servers`. EvalBench translates `httpUrl` to agy's
native `serverUrl` and writes `<fake_home>/.gemini/config/mcp_config.json`:

```yaml
setup:
  mcp_servers:
    cloud-sql:
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
```

EvalBench pre-verifies MCP attachment during generator initialization by probing
the CLI and confirming that tool schemas were materialized under
`<fake_home>/.gemini/antigravity-cli/mcp/<server>/`.

### Skills

Skills are delivered as plugin bundles under `setup.skills`. EvalBench invokes
`agy plugin install <target>` to install local plugin directories or remote git
repositories:

```yaml
setup:
  skills:
    # Local directory
    - "/path/to/local-plugin"

    # Git repository with branch/tag pinning
    - action: install_from_repo
      url: "https://github.com/gemini-cli-extensions/cloud-sql-postgresql.git#v1.2.3"
```

### Fake MCP Servers (Testing)

For offline testing without live network or cloud endpoints, define a local
stdio MCP server in `setup.fake_mcp_servers` with mock tools in
`fake_mcp_tools`. See
[`datasets/model_configs/agy_cli_fake_model.yaml`](../datasets/model_configs/agy_cli_fake_model.yaml).

---

## Scorers & Reporting

* **Scorers**: See the [scorer reference](scorers.md#agentic-scorers) for full
  configuration options on trajectory matching, goal completion, and turn metrics.
* **Reporting**: Exports to local CSV files (`reporting.csv.output_directory`)
  and Google BigQuery (`reporting.bigquery.gcp_project_id`).

---

## Comparison Reference (vs. Gemini CLI)

For teams familiar with the Gemini CLI harness, this table summarizes key
operational differences:

| Area | Gemini CLI | Antigravity (agy) CLI |
|------|------------|-----------------------|
| Installation | `npm install -g @google/gemini-cli@<ver>` | Auto-staged into `<fake_home>/.local/bin/` |
| Invocation | `npm exec @google/gemini-cli -- ...` | `agy -p <prompt> --dangerously-skip-permissions` |
| Output Format | `--output-format stream-json` | `--output-format stream-json` |
| Session Resume | `--resume <id>` | `--continue` |
| Settings Path | `~/.gemini/settings.json` | `~/.gemini/antigravity-cli/settings.json` |
| MCP Config | `mcpServers` in `settings.json` | `mcpServers` in `~/.gemini/config/mcp_config.json` |
| MCP Tool Naming | `mcp_<server>_<tool>` | `call_mcp_tool` wrapper (canonicalized to `<server>__<tool>`) |
| Skill Registration | `gemini skills <cmd>` | `agy plugin install <target>` |
| Model Parameter | `GEMINI_API_MODEL` env var | `--model` flag (UI label or slug) |
| Authentication | Token + ADC | Non-interactive ADC (`AGY_ADC_AUTH=true`) |

---

## Troubleshooting

### Authentication Errors / Interactive Login Prompt
* Ensure fresh ADC credentials exist by running `gcloud auth application-default login`.

### Model Authorization / `invalid model selection`
* Ensure the configured `model` in `model_config.yaml` is supported under ADC
  (use `Gemini 3.1 Pro (Low)` or Flash models, not `Gemini 3.1 Pro (High)`).

### MCP Server Fails to Attach
* Confirm `httpUrl` is valid and points to an active MCP endpoint.
* For Google APIs, ensure `authProviderType: google_credentials` is set and ADC is active.

### Skill / Plugin Not Loaded
* Inspect setup logs for `agy plugin install '<target>' failed` for the exit code
  and stderr. Ensure the target directory contains a valid manifest (`plugin.json`
  or `gemini-extension.json`).

### Empty Responses
* Ensure `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` are configured in the
  `env` block of your model config.

