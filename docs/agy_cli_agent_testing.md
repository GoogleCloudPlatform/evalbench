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
- [Authentication](#authentication)
- [Tool Paradigms](#tool-paradigms)
  - [MCP Servers](#mcp-servers)
  - [Skills](#skills)
  - [Fake MCP Servers (Testing)](#fake-mcp-servers-testing)
- [Scorers](#scorers)
- [Reporting](#reporting)
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
- **Built-in scoring** for trajectory matching, goal completion, and
  behavioral metrics.
- **Reporting** to local CSV files and Google BigQuery.

---

## Architecture

The evaluation pipeline coordinates test scenarios, user simulation, and the
Antigravity CLI execution environment:

```
Run Config -> AgentOrchestrator -> AgentEvaluator -> AgyCliGenerator -> agy
                                                       |
                                                       v
                                              MCP servers / skills
```

1. **AgentOrchestrator** (`orchestrator: agent`) loads the dataset and
   dispatches evaluation scenarios.
2. **AgentEvaluator** manages the multi-turn interaction loop between the
   tested model generator and the **SimulatedUser** LLM.
3. **AgyCliGenerator** (`generator: agy_cli`) manages the sandboxed agy CLI
   process, stages credentials and MCP configurations, runs invocations
   non-interactively via `--output-format stream-json`, and extracts tool
   calls, responses, and token stats.

---

## Prerequisites

1. **Python 3.10+** and project dependencies installed using `uv`:
   ```bash
   cd evalbench
   uv sync
   ```

2. **GCP Authentication** (Application Default Credentials for agy and
   Google-authenticated MCP servers):
   ```bash
   gcloud auth application-default login
   ```

3. **Environment Variables**:
   ```bash
   export EVAL_GCP_PROJECT_ID=your_project_id
   export EVAL_GCP_PROJECT_REGION=global
   ```

> [!NOTE]
> **You do not need `agy` installed on your host.** EvalBench installs its
> own isolated copy into `<fake_home>/.local/bin/` per session and always runs
> that sandboxed binary. Install `agy` on your host only if you wish to run
> `agy models` manually to inspect available model labels:
> ```bash
> curl -fsSL https://antigravity.google/cli/install.sh | sh -s -- --dir ~/.local/bin
> ```

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
| `dataset_format` | Yes | `agent-format` (recommended) or `gemini-cli-format` |
| `orchestrator` | Yes | `agent` (recommended) or `geminicli` |
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

The model configuration file specifies the generator type, model label,
timeouts, and tool setup:

| Key | Required | Description |
|-----|----------|-------------|
| `generator` | Yes | Must be `agy_cli` |
| `model` | Optional | Model label as listed by `agy models`, e.g. `"Gemini 3.1 Pro (Low)"`. If omitted, agy's default model is used. |
| `timeout` | Optional | Timeout duration string, e.g. `"20m"`. Passed via the `--print-timeout` flag (defaults to agy's internal 5m timeout). |
| `env` | Optional | Environment variables passed to the CLI process |
| `setup` | Optional | Tool setup block containing `mcp_servers`, `skills`, or `fake_mcp_servers` |

> [!IMPORTANT]
> **Project Configuration Required:** agy resolves its backend project from
> `settings.json`, which EvalBench populates from your model config. Always
> specify `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` inside the `env`
> block of your model config.

---

### 3. Evaluation Dataset (Evalset)

Evalsets define the testing scenarios using the standard agentic schema. See
the [agentic dataset format](configs/agentic-dataset-config.md) for field
definitions. Expected tool trajectories use the canonical `<server>__<tool>`
format.

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

## Authentication

agy authenticates non-interactively using **Google Application Default
Credentials (ADC)**. EvalBench enables this by setting `AGY_ADC_AUTH=true` in
the execution environment. The same ADC credentials supply outbound
authorization for `authProviderType: google_credentials` MCP servers.

Interactive OAuth login is intentionally disabled to keep evaluations hermetic
and non-interactive. Note that ADC may have access to a distinct set of model
slugs compared to user-interactive OAuth; run `agy models` with ADC active to
view available models.

---

## Tool Paradigms

### MCP Servers

Standalone MCP servers are configured under `setup.mcp_servers`. EvalBench
translates `httpUrl` entries to agy's native `serverUrl` and writes the
configuration to `<fake_home>/.gemini/config/mcp_config.json`:

```yaml
setup:
  mcp_servers:
    cloud-sql:
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
```

EvalBench automatically pre-verifies MCP attachment during generator
initialization: it executes a probe command and validates that tool schemas
were materialized under `<fake_home>/.gemini/antigravity-cli/mcp/<server>/`. If
a server fails to attach or discovers zero tools, evaluation halts immediately
with a diagnostic error rather than silently degrading to shell commands.

### Skills

Skills are delivered as plugin bundles under `setup.skills`. EvalBench invokes
`agy plugin install <target>` to register each skill, supporting local
directory paths and remote git repositories:

```yaml
setup:
  skills:
    # String format (local directory or git URL)
    - "/path/to/local-plugin"

    # Dict format with branch/tag pinning
    - action: install_from_repo
      url: "https://github.com/gemini-cli-extensions/cloud-sql-postgresql.git#v1.2.3"
```

Installed plugins are materialized under
`<fake_home>/.gemini/config/plugins/<name>/` and registered in
`<fake_home>/.gemini/config/import_manifest.json`.

### Fake MCP Servers (Testing)

For offline testing without live network or cloud endpoints, define a local
stdio MCP server in `setup.fake_mcp_servers` with mock tools in
`fake_mcp_tools`. See
[`datasets/model_configs/agy_cli_fake_model.yaml`](../datasets/model_configs/agy_cli_fake_model.yaml)
for a complete example.

---

## Scorers

See the [scorer reference](scorers.md#agentic-scorers) for the full catalog
of supported agentic scorers and configuration options.

---

## Reporting

Results are aggregated per scenario and exported according to your `reporting`
block:

* **CSV**: Generates scenario breakdowns and turn metrics under
  `reporting.csv.output_directory`.
* **BigQuery**: Uploads structured runs and scorer results to
  `reporting.bigquery.gcp_project_id`.

---

## Comparison Reference (vs. Gemini CLI)

For teams familiar with the Gemini CLI harness, this table summarizes key
operational differences:

| Area | Gemini CLI | Antigravity (agy) CLI |
|------|------------|-----------------------|
| Installation | `npm install -g @google/gemini-cli@<ver>` | Installer script staged into `<fake_home>/.local/bin/` |
| Invocation | `npm exec @google/gemini-cli -- ...` | `agy -p <prompt> --dangerously-skip-permissions` |
| Output Format | `--output-format stream-json` | `--output-format stream-json` |
| Session Resume | `--resume <id>` | `--continue` |
| Settings Path | `~/.gemini/settings.json` | `~/.gemini/antigravity-cli/settings.json` |
| MCP Config | `mcpServers` in `settings.json` | `mcpServers` in `~/.gemini/config/mcp_config.json` |
| MCP Tool Naming | `mcp_<server>_<tool>` | `call_mcp_tool` wrapper (canonicalized to `<server>__<tool>`) |
| Skill Registration | `gemini skills <cmd>` | `agy plugin install <target>` |
| Model Parameter | `GEMINI_API_MODEL` env var | `--model` flag (UI label from `agy models`) |
| Authentication | Token + ADC | Non-interactive ADC (`AGY_ADC_AUTH=true`) |

---

## Troubleshooting

### Prompted for interactive login / `authentication timed out`
* agy fell back to interactive login because ADC credentials were missing or
  unreadable.
* Run `gcloud auth application-default login` on your host machine to generate
  fresh credentials.

### `invalid model selection`
* The model label specified in `model_config.yaml` is not recognized by agy
  under the active credentials.
* Run `agy models` to view the accepted model labels and update your `model:`
  configuration key.

### MCP Server Fails to Attach
* **Check the URL parameter**: Ensure the server specifies `httpUrl` or
  `serverUrl`.
* **Verify Cache Files**: Check that tool schemas were created under
  `<fake_home>/.gemini/antigravity-cli/mcp/<server>/*.json`.
* **Check Auth**: For Google-authenticated endpoints, ensure
  `authProviderType: google_credentials` is specified and ADC is active.

### Skill / Plugin Not Loaded
* Check `<fake_home>/.gemini/config/import_manifest.json` to see if the plugin
  was registered.
* Check setup logs for `agy plugin install '<target>' failed` to inspect the
  exit code and stderr. Ensure the target directory or repository contains a
  valid plugin manifest (`plugin.json` or `gemini-extension.json`).

### Empty Responses
* Ensure `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` are set in the
  `env` section of your model config.
* Confirm `dataset_format` is set to `agent-format`.

