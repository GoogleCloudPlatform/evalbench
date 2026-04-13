# Claude Code Evaluation Guide

This guide covers how to use EvalBench for evaluating **Claude Code** agent workflows using **MCP Servers** (HTTP and stdio). It includes configuration reference, evaluation dataset format, scoring metrics, and step-by-step instructions for running evaluations locally.

The Claude Code generator mirrors the Gemini CLI generator in this repo — same evalset format, same orchestrator, same scorers — so most of what you know about [Gemini CLI evaluation](./gemini_cli_agent_testing.md) carries over.

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
  - [Vertex AI](#vertex-ai)
  - [Direct Anthropic API](#direct-anthropic-api)
- [MCP Servers](#mcp-servers)
- [Scorers](#scorers)
- [End-to-End Examples](#end-to-end-examples)
- [Troubleshooting](#troubleshooting)

---

## Overview

EvalBench's Claude Code integration enables automated, multi-turn evaluation of agentic AI workflows powered by Anthropic's [Claude Code CLI](https://docs.claude.com/en/docs/claude-code/overview). The CLI acts as the orchestrator that connects to MCP server backends and executes scenarios defined in an evaluation dataset. A **simulated user** powered by an LLM drives multi-turn conversations following a conversation plan.

### Key Capabilities

- **Multi-turn evaluation** with LLM-powered simulated users
- **Two auth modes**: Vertex AI (GCP ADC) or direct Anthropic API key
- **Two MCP transport modes**: HTTP/SSE (with Google Cloud OAuth auto-injection) and stdio
- **Pinned CLI versions** via `npm exec` (matches Gemini CLI's pattern)
- **8 built-in scorers** covering correctness, efficiency, and behavior quality
- **CSV and BigQuery reporting**

### What carries over from the Gemini CLI integration

| Aspect | Same / Different |
|---|---|
| Evalset JSON format | **Same** — `scenarios[]` with `id`, `starting_prompt`, `conversation_plan`, `expected_trajectory`, `max_turns`, `env` |
| `dataset_format` | New: `agent-format` (or keep `gemini-cli-format` — both work) |
| `orchestrator` | New: `agent` (or keep `geminicli` — both work) |
| Scorers | **Same** (`trajectory_matcher`, `goal_completion`, `behavioral_metrics`, etc.) |
| Simulated user | **Same** (`simulated_user_model_config`) |
| Reporting | **Same** (CSV / BigQuery) |
| MCP server config | **Same** schema for HTTP servers (`httpUrl`, `authProviderType: google_credentials`, `headers`) — auto-translated to Claude Code's native format |

---

## Architecture

```
┌─────────────────────────────────────────────────────────────────────┐
│                          EvalBench Pipeline                        │
│                                                                     │
│  ┌──────────────┐    ┌──────────────────┐    ┌───────────────────┐  │
│  │  Run Config  │───▶│  AgentOrchestrator│───▶│  AgentEvaluator   │  │
│  │  (YAML)      │    │                  │    │                   │  │
│  └──────────────┘    └──────────────────┘    └────────┬──────────┘  │
│                                                       │             │
│  ┌──────────────┐              ┌──────────────────────┼──────────┐  │
│  │  Eval Dataset│              │     Per Scenario      │          │  │
│  │  (JSON)      │─────────────▶│                      ▼          │  │
│  └──────────────┘              │  ┌──────────────────────────┐   │  │
│                                │  │   ClaudeCodeGenerator     │   │  │
│  ┌──────────────┐              │  │  ┌──────────┐ ┌────────┐ │   │  │
│  │ Model Config │──────────────│─▶│  │MCP / API │ │Sim.    │ │   │  │
│  │ (YAML)       │              │  │  │ (Vertex) │ │User    │ │   │  │
│  └──────────────┘              │  │  └──────────┘ └────────┘ │   │  │
│                                │  └───────────┬──────────────┘   │  │
│                                │              │                  │  │
│                                │              ▼                  │  │
│                                │  ┌──────────────────────────┐   │  │
│                                │  │   Scorers (8 metrics)    │   │  │
│                                │  └──────────────────────────┘   │  │
│                                └─────────────────────────────────┘  │
│                                                                     │
│  ┌──────────────────────────────────────────────────────────────┐   │
│  │                  Reporting (CSV / BigQuery)                   │   │
│  └──────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────┘
```

**Flow:**
1. The **Run Config** ties together the dataset, model config, scorers, and reporting.
2. The **AgentOrchestrator** (`orchestrator: agent`) loads the `AgentEvaluator`.
3. The evaluator instantiates `ClaudeCodeGenerator` based on `generator: claude_code` in the model config.
4. For each scenario in the evalset, the evaluator runs a multi-turn loop:
   - Sends the starting prompt to Claude Code via `npm exec --yes <version> -- -p <prompt>`
   - A **SimulatedUser** (LLM) generates realistic follow-up responses
   - Tools and stats are accumulated across turns from the `stream-json` output
   - Conversation continues until `max_turns` is reached or the simulated user sends `TERMINATE`
5. Results are scored and written to CSV and/or BigQuery.

---

## Prerequisites

1. **Python 3.10+** and project dependencies installed
2. **Node.js and npm** (for running Claude Code via `npm exec`)
3. **Claude Code CLI** — either:
   - Globally installed: `npm install -g @anthropic-ai/claude-code` (then use `claude_code_version: "claude"`), or
   - Pinned version (recommended for reproducibility): `claude_code_version: "@anthropic-ai/claude-code@2.1.85"` — `npm exec` will install it on first use
4. **Authentication** — either:
   - **Vertex AI**: `gcloud auth application-default login` and Claude models enabled in your GCP project's Model Garden
   - **Direct API**: `export ANTHROPIC_API_KEY=sk-ant-...`
5. **Environment variables** for the simulated user / scorer model:
   ```bash
   export EVAL_GCP_PROJECT_ID=your_project_id
   export EVAL_GCP_PROJECT_REGION=us-central1
   ```

---

## Quick Start

### 1. Choose a run config

```bash
# Real MCP server (Cloud SQL Admin API):
export EVAL_CONFIG=datasets/claude-code-tools/example_run_config.yaml

# Fake MCP (offline testing):
export EVAL_CONFIG=datasets/claude-code-tools/example_run_fake_config.yaml
```

### 2. Run the evaluation

```bash
./evalbench/run.sh
```

Results land in `results/<job_id>/` as CSV files.

---

## Configuration Reference

### 1. Run Configuration

The top-level config that ties everything together.

| Key | Required | Description |
|-----|----------|-------------|
| `dataset_config` | Yes | Path to the evalset JSON file |
| `dataset_format` | Yes | `agent-format` (or `gemini-cli-format` — alias) |
| `orchestrator` | Yes | `agent` (or `geminicli` — alias) |
| `model_config` | Yes | Path to the Claude Code model config YAML |
| `simulated_user_model_config` | Yes | Path to the model config for the simulated user LLM |
| `scorers` | Yes | Dictionary of scorer configurations |
| `runners.agent_runners` | Optional | Concurrency (default `10`). Set to `1` for sequential runs. |
| `reporting` | Optional | CSV and/or BigQuery output options |

**Example** ([example_run_config.yaml](../datasets/claude-code-tools/example_run_config.yaml)):

```yaml
dataset_config: datasets/claude-code-tools/claude-code.evalset.json
dataset_format: agent-format

orchestrator: agent
model_config: datasets/model_configs/claude_code_model.yaml
simulated_user_model_config: datasets/model_configs/gemini_2.5_pro_model.yaml

# Run scenarios sequentially (default is 10 in parallel)
runners:
  agent_runners: 1

scorers:
  trajectory_matcher: {}
  goal_completion:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
  behavioral_metrics:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
  parameter_analysis:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
  turn_count: {}
  end_to_end_latency: {}
  tool_call_latency: {}
  token_consumption: {}

reporting:
  csv:
    output_directory: 'results'
```

---

### 2. Model Configuration

The model config defines the Claude Code CLI version, model, auth, environment, and MCP server setup.

#### Common Fields

| Key | Required | Description |
|-----|----------|-------------|
| `claude_code_version` | Yes | Either `"claude"` (uses the globally installed binary) or an npm spec like `"@anthropic-ai/claude-code@2.1.85"` (uses `npm exec --yes`) |
| `generator` | Yes | Must be `claude_code` |
| `model` | Yes | Model ID (see [Authentication](#authentication) for valid IDs) |
| `use_vertex` | Optional | `true` to route through Vertex AI; `false`/omit for direct Anthropic API |
| `vertex_project_id` | If `use_vertex` | GCP project for Vertex AI |
| `vertex_region` | If `use_vertex` | Vertex region (e.g., `us-east5`) |
| `env` | Optional | Environment variables passed to the CLI process |
| `setup.mcp_servers` | Optional | MCP server configurations (see [MCP Servers](#mcp-servers)) |
| `allowed_tools` | Optional | List of tool names to allow (e.g., `["Bash", "mcp__cloud-sql"]`) |

---

### 3. Evaluation Dataset (Evalset)

**Identical schema** to the Gemini CLI evalset. See [Gemini CLI doc — Evalset](./gemini_cli_agent_testing.md#3-evaluation-dataset-evalset) for details.

Minimal example:

```json
{
  "scenarios": [
    {
      "id": "cloud-sql-list-instances-01",
      "starting_prompt": "list all Cloud SQL instances in project astana-evaluation",
      "conversation_plan": "Ask the agent to list instances. If nl2code exists, get its state and verify it is RUNNABLE.",
      "expected_trajectory": ["list_instances", "get_instance"],
      "env": { "GOOGLE_CLOUD_PROJECT": "astana-evaluation" },
      "kind": "tools",
      "max_turns": 3
    }
  ]
}
```

---

## Authentication

Claude Code supports two auth modes, controlled by `use_vertex` in the model config.

### Vertex AI

Recommended on GCP — uses Application Default Credentials, no API key needed.

```yaml
use_vertex: true
vertex_project_id: "astana-evaluation"
vertex_region: "us-east5"
model: "claude-opus-4-6"   # Vertex model ID format
```

**Vertex model IDs** (no date suffix, or `@YYYYMMDD`):
- `claude-opus-4-6`
- `claude-sonnet-4@20250514`

**Requirements:**
- Run `gcloud auth application-default login` (locally) or use a service account with Vertex AI User role (Cloud Build / GKE)
- Claude models must be enabled in your GCP project via Model Garden
- Sets these env vars under the hood: `CLAUDE_CODE_USE_VERTEX=1`, `ANTHROPIC_VERTEX_PROJECT_ID`, `CLOUD_ML_REGION`

### Direct Anthropic API

```yaml
use_vertex: false
model: "claude-opus-4-20250514"   # Direct API model ID format
env:
  ANTHROPIC_API_KEY: "sk-ant-..."   # OR export it in your shell
```

**Direct API model IDs** (with date suffix):
- `claude-opus-4-20250514`
- `claude-sonnet-4-20250514`

> **Tip**: Don't commit API keys into `model_config.yaml`. Prefer `export ANTHROPIC_API_KEY=...` in your shell, or use Secret Manager when running on Cloud Build / GKE.

---

## MCP Servers

EvalBench accepts the **same MCP server config schema as Gemini CLI** for HTTP servers. The Claude Code generator auto-translates Gemini-style fields into Claude Code's native format at runtime:

| Gemini-style field | Claude Code translation |
|---|---|
| `httpUrl` | → `url` + auto-adds `type: "http"` |
| `authProviderType: google_credentials` | → fetches a token via `gcloud auth print-access-token` and injects `Authorization: Bearer <token>` into headers |
| `oauth.scopes` | (dropped — Claude Code doesn't use Gemini's OAuth delegation) |
| `headers` | → passed through as-is |
| `command` / `args` (stdio) | → passed through as-is |

### HTTP MCP server (Cloud SQL Managed)

```yaml
setup:
  mcp_servers:
    "cloud-sql":
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
      oauth:
        scopes:
        - https://www.googleapis.com/auth/cloud-platform
      headers:
        X-Goog-User-Project: astana-evaluation
```

This generates the following `mcp_servers.json` for Claude Code:

```json
{
  "mcpServers": {
    "cloud-sql": {
      "type": "http",
      "url": "https://sqladmin.googleapis.com/mcp",
      "headers": {
        "X-Goog-User-Project": "astana-evaluation",
      }
    }
  }
}
```

### Stdio MCP server

```yaml
setup:
  mcp_servers:
    "my-server":
      command: "python"
      args:
        - "path/to/server.py"
        - "--some-flag"
```

### How it works under the hood

1. `ClaudeCodeGenerator._setup_mcp_servers` writes the translated config to `<fake_home>/.claude/mcp_servers.json`
2. The CLI is invoked with `--mcp-config <path>` so it loads only the configured servers (no host-machine pollution)
3. Each scenario runs in a sandboxed `HOME` (`.venv/fake_home_claude/` locally, `/tmp_sessions/<session_id>/fake_home` in gRPC mode)

---

## Scorers

**Identical** to the Gemini CLI scorers. See [Gemini CLI doc — Scorers](./gemini_cli_agent_testing.md#scorers) for the full list.

Quick reference:

| Scorer | Type | Description |
|---|---|---|
| `trajectory_matcher` | Deterministic | Jaccard or Levenshtein match between expected and actual tool trajectory |
| `goal_completion` | LLM | Did the agent accomplish the conversation plan? |
| `behavioral_metrics` | LLM | Hallucination rate + clarification rate |
| `parameter_analysis` | LLM | Qualitative feedback on tool parameters |
| `turn_count` | Deterministic | Number of conversation turns |
| `end_to_end_latency` | Deterministic | Total latency (model + tool execution) |
| `tool_call_latency` | Deterministic | Sum of tool execution durations |
| `token_consumption` | Deterministic | Total input + output tokens |

---

## End-to-End Examples

### Example 1: Vertex AI + Cloud SQL Managed MCP

**Goal**: Use Claude Opus on Vertex AI to manage Cloud SQL instances.

```yaml
# datasets/model_configs/claude_code_model.yaml
claude_code_version: "@anthropic-ai/claude-code@2.1.85"
generator: claude_code
model: "claude-opus-4-6"

use_vertex: true
vertex_project_id: "astana-evaluation"
vertex_region: "us-east5"

env:
  GOOGLE_CLOUD_PROJECT: "astana-evaluation"

setup:
  mcp_servers:
    "cloud-sql":
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
      oauth:
        scopes:
        - https://www.googleapis.com/auth/cloud-platform
      headers:
        X-Goog-User-Project: astana-evaluation
```

Run:

```bash
gcloud auth application-default login
export EVAL_GCP_PROJECT_ID=astana-evaluation
export EVAL_CONFIG=datasets/claude-code-tools/example_run_config.yaml
./evalbench/run.sh
```

### Example 2: Direct Anthropic API

```yaml
claude_code_version: "@anthropic-ai/claude-code@2.1.85"
generator: claude_code
model: "claude-opus-4-20250514"
use_vertex: false

setup:
  mcp_servers:
    "cloud-sql":
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
      headers:
        X-Goog-User-Project: astana-evaluation
```

Run:

```bash
export ANTHROPIC_API_KEY="sk-ant-..."
export EVAL_CONFIG=datasets/claude-code-tools/example_run_config.yaml
./evalbench/run.sh
```

---

## Troubleshooting

### `Error: Invalid MCP configuration: ... Does not adhere to MCP server configuration schema`

The MCP server config didn't translate correctly. Common causes:
- Missing both `httpUrl`/`url` and `command` — the server needs at least one
- Stale generated `mcp_servers.json` — delete `.venv/fake_home_claude/.claude/mcp_servers.json` and re-run

### `There's an issue with the selected model (...). It may not exist or you may not have access to it.`

- **Vertex AI**: The model isn't enabled in your GCP project. Visit Model Garden in the Cloud Console and enable it for your `vertex_region`. Or pick a different model that's already enabled (`claude-opus-4-6`, `claude-sonnet-4@20250514`).
- **Direct API**: Your `ANTHROPIC_API_KEY` doesn't have access to the model. Check your console at https://console.anthropic.com.

### `Not logged in · Please run /login`

Claude Code can't find auth credentials in the sandboxed `HOME`. Either:
- Use `use_vertex: true` (uses GCP ADC, no login needed), or
- Set `ANTHROPIC_API_KEY` in env or model config, or
- Make sure your real `~/.claude/` has valid credentials (the generator copies them to the fake home)

### `Error: --session-id can only be used with --continue or --resume if --fork-session is also specified.`

Already fixed — the generator passes `--fork-session` automatically when resuming. If you still see this, make sure you're on a current version of [claude_code.py](../evalbench/generators/models/claude_code.py).

### `When using --print, --output-format=stream-json requires --verbose`

Already fixed — `--verbose` is added automatically. Check your version of [claude_code.py](../evalbench/generators/models/claude_code.py).

### Empty/zero results in the summary

The simulated user failed to initialize. Check `EVAL_GCP_PROJECT_ID` is set if your simulated user model uses Vertex AI:

```bash
export EVAL_GCP_PROJECT_ID=astana-evaluation
```

### Scenarios appear to run in interleaved order

This is **expected** — the default `agent_runners: 10` runs scenarios concurrently. To force sequential execution, add this to your run config:

```yaml
runners:
  agent_runners: 1
```

### MCP server fails with `401 Unauthorized` (real Cloud SQL endpoint)

The injected gcloud access token may have insufficient scopes. Make sure `gcloud auth application-default login` was run with `--scopes=https://www.googleapis.com/auth/cloud-platform`, and your account has the required IAM roles (e.g., `roles/cloudsql.admin`).

### `npm exec` is slow on first run

`npm exec --yes <package>@<version>` downloads the package on first use (~30 sec). Subsequent runs use the cache.

---

## See Also

- [Gemini CLI Evaluation Guide](./gemini_cli_agent_testing.md) — sister doc, shares most concepts
- [Claude Code CLI docs](https://docs.claude.com/en/docs/claude-code/overview) — official CLI reference
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) — protocol used by tool servers
