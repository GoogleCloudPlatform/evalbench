# OpenAI Codex CLI Evaluation Guide

This guide covers how to use EvalBench for evaluating **OpenAI Codex CLI** (`codex exec`) agent workflows using **MCP Servers** (HTTP/streamable and stdio). It includes configuration reference, evaluation dataset format, scoring metrics, and step-by-step instructions for running evaluations locally.

The Codex CLI generator mirrors the Gemini CLI and Claude Code generators in this repo — same evalset format, same orchestrator, same scorers — so most of what you know about [Gemini CLI evaluation](./gemini_cli_agent_testing.md) and [Claude Code evaluation](./claude_code_agent_testing.md) carries over.

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
- [MCP Servers](#mcp-servers)
- [Sandbox & Approval Modes](#sandbox--approval-modes)
- [Pricing & Cost Tracking](#pricing--cost-tracking)
- [Scorers](#scorers)
- [End-to-End Examples](#end-to-end-examples)
- [Troubleshooting](#troubleshooting)

---

## Overview

EvalBench's Codex CLI integration enables automated, multi-turn evaluation of agentic AI workflows powered by OpenAI's [Codex CLI](https://github.com/openai/codex). The CLI acts as the orchestrator that connects to MCP server backends and executes scenarios defined in an evaluation dataset. A **simulated user** powered by an LLM drives multi-turn conversations following a conversation plan.

### Key Capabilities

- **Multi-turn evaluation** with LLM-powered simulated users (uses `codex exec resume <session_id>` to continue prior threads)
- **API-key auth** sourced from `OPENAI_API_KEY` env or Google Secret Manager
- **Two MCP transport modes**: streamable HTTP (with Google Cloud OAuth auto-injection) and stdio
- **Pinned CLI versions** via `npm exec` (matches Gemini CLI / Claude Code)
- **Cost tracking** via configurable per-model pricing (Codex NDJSON ships tokens but not USD)
- **8 built-in scorers** covering correctness, efficiency, and behavior quality
- **CSV and BigQuery reporting**

### What carries over from the other agent integrations

| Aspect | Same / Different |
|---|---|
| Evalset JSON format | **Same** — `scenarios[]` with `id`, `starting_prompt`, `conversation_plan`, `expected_trajectory`, `max_turns`, `env` |
| `dataset_format` | `agent-format` |
| `orchestrator` | `agent` |
| Scorers | **Same** (`trajectory_matcher`, `goal_completion`, `behavioral_metrics`, etc.) |
| Simulated user | **Same** (`simulated_user_model_config`) |
| Reporting | **Same** (CSV / BigQuery) |
| MCP server config | **Same** Gemini-style schema (`httpUrl`, `authProviderType: google_credentials`, `headers`) — auto-translated to Codex's TOML format |
| Skills & Extensions | **Same** — supports installing skills from git repos or local directories in the sandboxed environment |

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
│                                │  │   CodexCliGenerator       │   │  │
│  ┌──────────────┐              │  │  ┌──────────┐ ┌────────┐ │   │  │
│  │ Model Config │──────────────│─▶│  │ MCP /    │ │Sim.    │ │   │  │
│  │ (YAML)       │              │  │  │ codex CLI│ │User    │ │   │  │
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
3. The evaluator instantiates [CodexCliGenerator](../evalbench/generators/models/codex_cli.py) based on `generator: codex_cli` in the model config.
4. On startup, the generator writes a sandboxed `~/.codex/config.toml` (with translated MCP servers) and `~/.codex/auth.json` (with the API key) into `.venv/fake_home_codex/` so the host machine's `~/.codex` is not touched.
5. For each scenario, the evaluator runs a multi-turn loop:
   - Sends the starting prompt to Codex via `codex exec --json --skip-git-repo-check ... <prompt>`
   - A **SimulatedUser** (LLM) generates realistic follow-up responses
   - Subsequent turns use `codex exec resume <session_id>` to continue the same Codex thread
   - Tools and stats are accumulated across turns from the NDJSON ThreadEvent stream
   - Conversation continues until `max_turns` is reached or the simulated user sends `TERMINATE`
6. Results are scored and written to CSV and/or BigQuery.

---

## Prerequisites

1. **Python 3.10+** and project dependencies installed
2. **Node.js and npm** (for running Codex CLI via `npm exec`)
3. **Codex CLI** — either:
   - Globally installed: `npm install -g @openai/codex` (then use `codex_cli_version: "codex"`), or
   - Pinned version (recommended for reproducibility): `codex_cli_version: "@openai/codex@latest"` (or a specific version like `"@openai/codex@0.30.0"`) — `npm exec --yes` will install it on first use
4. **OpenAI API key** — see [Authentication](#authentication). The key must be supplied in the model config (or env). Codex's ChatGPT-OAuth fallback is **not** usable from CI — accounts without ChatGPT-Plus get `402 deactivated_workspace`.
5. **Environment variables** for the simulated user / scorer model:
   ```bash
   export EVAL_GCP_PROJECT_ID=your_project_id
   export EVAL_GCP_PROJECT_REGION=us-central1
   ```
6. **gcloud** (only if any MCP server uses `authProviderType: google_credentials` — the generator shells out to `gcloud auth print-access-token`)

---

## Quick Start

### 1. Choose a run config

```bash
# Real MCP server (Cloud SQL Admin API):
export EVAL_CONFIG=datasets/codex-cli-tools/example_run_config.yaml

# Fake MCP (offline testing, deterministic):
export EVAL_CONFIG=datasets/codex-cli-tools/example_run_fake_config.yaml
```

### 2. Run the evaluation

```bash
./evalbench/run.sh
```

Results land in `results/<job_id>/` as CSV files.

---

## Configuration Reference

### 1. Run Configuration

The top-level config that ties everything together. **Identical** to the other agent run configs.

| Key | Required | Description |
|-----|----------|-------------|
| `dataset_config` | Yes | Path to the evalset JSON file |
| `dataset_format` | Yes | `agent-format` |
| `orchestrator` | Yes | `agent` |
| `model_config` | Yes | Path to the Codex CLI model config YAML |
| `simulated_user_model_config` | Yes | Path to the model config for the simulated user LLM |
| `scorers` | Yes | Dictionary of scorer configurations |
| `runners.agent_runners` | Optional | Concurrency (default `10`). Set to `1` for sequential runs — recommended for Codex because all scenarios share the same sandboxed `~/.codex` store. |
| `reporting` | Optional | CSV and/or BigQuery output options |

**Example** ([example_run_config.yaml](../datasets/codex-cli-tools/example_run_config.yaml)):

```yaml
dataset_config: datasets/codex-cli-tools/codex-cli.evalset.json
dataset_format: agent-format

orchestrator: agent
model_config: datasets/model_configs/codex_cli_model.yaml
simulated_user_model_config: datasets/model_configs/gemini_2.5_pro_model.yaml

# Run scenarios sequentially. Codex shares one ~/.codex/config.toml across
# the runner pool; serial runs avoid session-id collisions on `codex exec resume`.
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

The model config defines the Codex CLI version, model, auth, sandbox/approval policy, environment, MCP server setup, and pricing.

#### Common Fields

| Key | Required | Description |
|-----|----------|-------------|
| `codex_cli_version` | Yes | Either `"codex"` (uses the globally installed binary) or an npm spec like `"@openai/codex@latest"` / `"@openai/codex@0.30.0"` (uses `npm exec --yes`) |
| `generator` | Yes | Must be `codex_cli` |
| `model` | Yes | Model id passed to `codex exec -m <model>` (e.g., `"gpt-5.5"`, `"o4-mini"`, `"gpt-4.1"`) |
| `openai_api_key_secret` | Optional | Google Secret Manager resource path for the API key. Bare form `projects/.../secrets/.../versions/<N>` or `secret_manager://projects/.../secrets/.../versions/<N>` URL form. **Numeric version required — `latest` not supported.** |
| `env.OPENAI_API_KEY` | Optional | Direct API key. Also accepts a Secret Manager path here (auto-detected and resolved). Prefer `openai_api_key_secret` or shell env over hardcoding. |
| `sandbox_mode` | Optional | `"danger-full-access"` (default — passes `--dangerously-bypass-approvals-and-sandbox`), or any value Codex's `--sandbox` flag accepts (e.g., `"workspace-write"`, `"read-only"`) |
| `approval_mode` | Optional | Forwarded to `--ask-for-approval` when `sandbox_mode != "danger-full-access"`. Default `"never"`. |
| `profile` | Optional | Codex profile name (forwarded as `--profile <name>`) |
| `json_flag` | Optional | `"--json"` (default, newer Codex versions) or `"--experimental-json"` (older versions). Codex requires NDJSON for the eval pipeline to extract tool calls and tokens. |
| `pricing` | Optional | Per-model rates used to compute `cost_usd` per turn. See [Pricing & Cost Tracking](#pricing--cost-tracking). |
| `env` | Optional | Environment variables passed to the CLI process (e.g., `GOOGLE_CLOUD_PROJECT` for Cloud SQL MCP) |
| `setup.mcp_servers` | Optional | MCP server configurations (see [MCP Servers](#mcp-servers)) |
| `setup.config` | Optional | Free-form key/value pairs written to the top of `~/.codex/config.toml`. Merged on top of the default `forced_login_method = "api"`. |

**Example** ([codex_cli_model.yaml](../datasets/model_configs/codex_cli_model.yaml)):

```yaml
codex_cli_version: "@openai/codex@latest"
generator: codex_cli
model: "gpt-5.5"

openai_api_key_secret: <secret>

pricing:
  input_per_million_usd:        1.25
  cached_input_per_million_usd: 0.125
  output_per_million_usd:       10.0

env:
  GOOGLE_CLOUD_PROJECT: "astana-evaluation"
  GOOGLE_CLOUD_LOCATION: "us-central1"

setup:
  mcp_servers:
    "cloud-sql":
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
      headers:
        X-Goog-User-Project: astana-evaluation
```

---

### 3. Evaluation Dataset (Evalset)

**Identical schema** to the Gemini CLI / Claude Code evalset. See [Gemini CLI doc — Evalset](./gemini_cli_agent_testing.md#3-evaluation-dataset-evalset) for full details.

Minimal example ([codex-cli.evalset.json](../datasets/codex-cli-tools/codex-cli.evalset.json)):

```json
{
  "scenarios": [
    {
      "id": "cloud-sql-list-instances-01",
      "starting_prompt": "list all Cloud SQL instances in project astana-evaluation",
      "conversation_plan": "Ask the agent to list instances in project astana-evaluation. Once all instances are listed if nl2code exists get its state and validate it is RUNNABLE.",
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

Codex CLI uses an OpenAI API key. The generator resolves it in this order, then writes it to `~/.codex/auth.json` (the same file `codex login --api-key` produces) so `codex exec` picks it up:

1. **`openai_api_key_secret`** in the model config — a Google Secret Manager resource path. Bare form `projects/<num>/secrets/<NAME>/versions/<N>` or `secret_manager://projects/...` URL form. Requires a numeric version (no `latest`).
2. **`env.OPENAI_API_KEY`** in the model config, or **`OPENAI_API_KEY`** in the shell env. If the value itself looks like a Secret Manager path, it's resolved transparently.

```yaml
# Option 1: Secret Manager (recommended for shared / CI environments)
openai_api_key_secret: <secret>

# Option 2: shell env (recommended for local dev)
# export OPENAI_API_KEY=sk-...
env: {}

# Option 3: inline (avoid — gets committed)
env:
  OPENAI_API_KEY: "sk-..."
```

The generator writes `<fake_home>/.codex/auth.json` with `{"auth_mode": "apikey", "OPENAI_API_KEY": "<key>"}` and chmods it to `0600`. It also sets `forced_login_method = "api"` in `config.toml` so Codex never tries the ChatGPT-OAuth flow.

> **Why not just rely on the env var?** Codex's auth manager only honors `OPENAI_API_KEY` when an internal `enable_codex_api_key_env` flag is set. For `codex exec` the canonical path is `auth.json`, so the generator writes both.

---

## MCP Servers

EvalBench accepts the **same MCP server config schema as Gemini CLI and Claude Code**. The Codex generator auto-translates it into Codex's TOML schema at runtime (see [_translate_mcp_config](../evalbench/generators/models/codex_cli.py)):

| Gemini-style field | Codex translation |
|---|---|
| `httpUrl` | → `url` (TOML, streamable HTTP server) |
| `headers` | → `http_headers` (TOML inline table) |
| `authProviderType: google_credentials` | → fetches a token via `gcloud auth print-access-token` and injects `Authorization: Bearer <token>` into `http_headers` |
| `oauth.scopes` | (dropped — Codex doesn't use Gemini's OAuth delegation) |
| `command` / `args` / `env` / `cwd` (stdio) | → passed through as-is into a `[mcp_servers.NAME]` stdio block |

### HTTP MCP server (Cloud SQL Managed)

```yaml
setup:
  mcp_servers:
    "cloud-sql":
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
      headers:
        X-Goog-User-Project: astana-evaluation
```

This generates the following block in `~/.codex/config.toml`:

```toml
forced_login_method = "api"

[mcp_servers.cloud-sql]
url = "https://sqladmin.googleapis.com/mcp"
http_headers = { "X-Goog-User-Project" = "astana-evaluation", "Authorization" = "Bearer ya29...." }
```

### Stdio MCP server

```yaml
setup:
  mcp_servers:
    "cloud-sql":
      command: "python"
      args:
        - "evalbench/util/fake_mcp_server.py"
        - "--server-name"
        - "cloud-sql"
        - "--config"
        - "datasets/model_configs/codex_cli_fake_model.yaml"
```

→ TOML:

```toml
[mcp_servers.cloud-sql]
command = "python"
args = ["evalbench/util/fake_mcp_server.py", "--server-name", "cloud-sql", "--config", "datasets/model_configs/codex_cli_fake_model.yaml"]
```

### How it works under the hood

1. `CodexCliGenerator._setup` writes the translated config to `<fake_home>/.codex/config.toml`
2. `_write_codex_auth_json` writes the API key to `<fake_home>/.codex/auth.json`
3. The CLI is invoked with `HOME=<fake_home>` so it loads only the configured servers (no host-machine pollution)
4. Each scenario runs in a sandboxed `HOME` (`.venv/fake_home_codex/` locally, `/tmp_sessions/<session_id>/fake_home` in gRPC mode)

---

## Skills & Extensions

Codex CLI evaluations support **Skills** and **Extensions** (plugins). These are installed during the setup phase into the sandboxed `~/.codex` directory.

### Configuration

You can specify skills to install in the `setup` section of your model configuration:

```yaml
setup:
  skills:
    - action: install_from_repo
      url: "https://github.com/gemini-cli-extensions/cloud-sql-postgresql.git"
```

The generator will:
1. Clone the repository into `<fake_home>/.codex/plugins/`
2. Register the plugin in `<fake_home>/.codex/plugins/marketplace.json`
3. Install the individual skills into `<fake_home>/.codex/skills/`

These skills are then available for the Codex agent to use during the evaluation turns.

---

## Sandbox & Approval Modes

Codex CLI sandboxes file/network access by default and prompts for approval before tool calls. For automated evaluation, the generator disables both by default — equivalent to Gemini CLI's `--yolo` and Claude Code's `--dangerously-skip-permissions`.

| `sandbox_mode` value | Behavior | Resulting flags |
|---|---|---|
| `"danger-full-access"` (default) | Fully bypass sandbox + approvals | `--dangerously-bypass-approvals-and-sandbox` |
| `"workspace-write"` | Allow writes only inside the working tree | `--sandbox workspace-write --ask-for-approval <approval_mode>` |
| `"read-only"` | No writes anywhere | `--sandbox read-only --ask-for-approval <approval_mode>` |

`approval_mode` (default `"never"`) is forwarded as `--ask-for-approval <mode>` whenever `sandbox_mode` is not `danger-full-access`. Use `"never"` for unattended runs; any prompt-driven mode will hang the evaluator.

```yaml
# Default (most permissive — recommended for CI)
sandbox_mode: "danger-full-access"

# Stricter — block writes outside repo, never prompt
sandbox_mode: "workspace-write"
approval_mode: "never"
```

---

## Pricing & Cost Tracking

Codex's NDJSON includes `usage.input_tokens`, `usage.output_tokens`, and `usage.cached_input_tokens` per turn but **does not** include cost. Provide a `pricing` block in the model config and the generator will compute `cost_usd` per turn for the `token_consumption` scorer.

Two equivalent forms are accepted; pick whichever is easier to copy from OpenAI's pricing page.

**Per-million form (matches the OpenAI pricing page):**

```yaml
pricing:
  input_per_million_usd:        1.25
  cached_input_per_million_usd: 0.125  # optional; defaults to 10% of input
  output_per_million_usd:       10.0
```

**Per-token form:**

```yaml
pricing:
  input_per_token_usd:        0.00000125
  cached_input_per_token_usd: 0.000000125
  output_per_token_usd:       0.00001
```

**Cost formula:**

```
billable_input = max(0, input_tokens - cached_input_tokens)
cost_usd = (billable_input * input_rate)
         + (cached_input_tokens * cached_input_rate)
         + (output_tokens * output_rate)
```

If `pricing` is missing or malformed, `cost_usd` is reported as `0.0` (rather than guessing). Update the rates whenever you change `model:` — pricing differs by model.

---

## Scorers

**Identical** to the Gemini CLI / Claude Code scorers. See [Gemini CLI doc — Scorers](./gemini_cli_agent_testing.md#scorers) for the full list.

Quick reference:

| Scorer | Type | Description |
|---|---|---|
| `trajectory_matcher` | Deterministic | Jaccard or Levenshtein match between expected and actual tool trajectory |
| `goal_completion` | LLM | Did the agent accomplish the conversation plan? |
| `behavioral_metrics` | LLM | Hallucination rate + clarification rate |
| `parameter_analysis` | LLM | Qualitative feedback on tool parameters |
| `turn_count` | Deterministic | Number of conversation turns |
| `end_to_end_latency` | Deterministic | Total wall-clock latency of the `codex exec` subprocess |
| `tool_call_latency` | Deterministic | Sum of per-tool durations measured between `item.started` and `item.completed` arrival times (Codex events carry no timestamps, so the generator stamps arrival in-process) |
| `token_consumption` | Deterministic | Total input + output + cached tokens, plus `cost_usd` from the `pricing` block |

The Codex generator extracts tool calls from the following NDJSON ThreadItem kinds: `mcp_tool_call`, `command_execution` (reported as `shell`), `web_search`, and `file_change`.

---

## End-to-End Examples

### Example 1: Real Cloud SQL Managed MCP

**Goal**: Use Codex to manage Cloud SQL instances via the public Cloud SQL Admin MCP.

```yaml
# datasets/model_configs/codex_cli_model.yaml
codex_cli_version: "@openai/codex@latest"
generator: codex_cli
model: "gpt-5.5"

openai_api_key_secret: "projects/393137573/secrets/OPENAI_API_KEY/versions/1"

pricing:
  input_per_million_usd:        1.25
  cached_input_per_million_usd: 0.125
  output_per_million_usd:       10.0

env:
  GOOGLE_CLOUD_PROJECT: "astana-evaluation"

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
gcloud auth login                           # for the OpenAI Secret Manager fetch
gcloud auth application-default login       # for the Cloud SQL MCP token
export EVAL_GCP_PROJECT_ID=astana-evaluation
export EVAL_CONFIG=datasets/codex-cli-tools/example_run_config.yaml
./evalbench/run.sh
```

### Example 2: Offline / fake MCP (deterministic)

Use the bundled fake MCP server when you want to exercise the agent without any network calls — useful in CI or when iterating on a scenario file.

```bash
export EVAL_CONFIG=datasets/codex-cli-tools/example_run_fake_config.yaml
./evalbench/run.sh
```

This config ([example_run_fake_config.yaml](../datasets/codex-cli-tools/example_run_fake_config.yaml)) launches `evalbench/util/fake_mcp_server.py` over stdio with the canned tool responses defined under `fake_mcp_tools` in [codex_cli_fake_model.yaml](../datasets/model_configs/codex_cli_fake_model.yaml).

### Example 3: Local dev with `OPENAI_API_KEY` from your shell

Skip Secret Manager entirely:

```yaml
codex_cli_version: "@openai/codex@latest"
generator: codex_cli
model: "gpt-5.5"
# no openai_api_key_secret — OPENAI_API_KEY picked up from shell env
setup:
  mcp_servers:
    "cloud-sql":
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
      headers:
        X-Goog-User-Project: astana-evaluation
```

```bash
export OPENAI_API_KEY=sk-...
export EVAL_CONFIG=datasets/codex-cli-tools/example_run_config.yaml
./evalbench/run.sh
```

---

## Troubleshooting

### `402 deactivated_workspace` / "ChatGPT-Plus required"

Codex fell back to ChatGPT-OAuth because no API key was found. Check:
- `openai_api_key_secret` resolves successfully (look for `Failed to fetch OPENAI_API_KEY from Secret Manager` in logs)
- Or `OPENAI_API_KEY` is set in your shell / model config `env`
- The generator logs `Codex API key resolved (length=N) and written to .../auth.json` on a successful resolve. If you see `Codex API key could not be resolved`, fix that first.

### `error: unexpected argument '--json' found` (or similar)

Your installed Codex predates the `--json` flag. Either upgrade (`codex_cli_version: "@openai/codex@latest"`) or fall back to the experimental flag:

```yaml
json_flag: "--experimental-json"
```

### `Error: command not found: codex` / `npm: command not found`

- For pinned versions (`@openai/codex@...`): make sure `node` and `npm` are on `PATH`. `npm exec --yes` will download Codex on first use.
- For `codex_cli_version: "codex"`: install globally with `npm install -g @openai/codex`.

### MCP server fails with `401 Unauthorized` (real Cloud SQL endpoint)

The injected gcloud access token has insufficient scopes or your principal lacks IAM access. Make sure:
- `gcloud auth application-default login` was run with `--scopes=https://www.googleapis.com/auth/cloud-platform`
- Your account / service account has the required IAM roles (e.g., `roles/cloudsql.admin`)
- `X-Goog-User-Project` header points at a project that has the Cloud SQL Admin API enabled

### `Invalid TOML` when Codex starts

Either a manual edit to `~/.codex/config.toml` clobbered the generated file, or a hand-written `setup.config` value contains an unsupported type. Fix:

```bash
rm -rf .venv/fake_home_codex
```

…and re-run. The generator regenerates `config.toml` on every invocation.

### `[error] thread <id> not found` events in stdout

These show up as `[error] ...` in the response field when `codex exec resume` is called against a thread that Codex couldn't write to. The matching `failed to record rollout items: thread <id> not found` lines on stderr are scrubbed by the generator (see `_STDERR_NOISE_PATTERNS`). Causes:
- Two scenarios racing on the same fake `HOME` — set `runners.agent_runners: 1`.
- A previous run left a corrupt `~/.codex/sessions/` — `rm -rf .venv/fake_home_codex/.codex/sessions` and re-run.

### Empty/zero results in the summary

The simulated user failed to initialize. Check `EVAL_GCP_PROJECT_ID` is set if your simulated user model uses Vertex AI:

```bash
export EVAL_GCP_PROJECT_ID=astana-evaluation
```

### `cost_usd` is always `0.0`

Either no `pricing` block was provided, or it's malformed. Check the model-config YAML and look for `Codex pricing config missing input/output rates; cost_usd will be 0.` in the logs. Pricing keys must be strict `<thing>_per_million_usd` or `<thing>_per_token_usd` — typos silently fall through.

### Scenarios appear to run in interleaved order

This is **expected** when `agent_runners > 1`. Codex shares one `~/.codex/config.toml` and one `auth.json` across the runner pool, so concurrent scenarios are safe **but** their NDJSON streams will interleave in the eval logs. To force sequential execution:

```yaml
runners:
  agent_runners: 1
```

### `npm exec` is slow on first run

`npm exec --yes @openai/codex@<version>` downloads the package on first use (~30–60 sec). Subsequent runs hit the npm cache.

### Cleaning the sandbox

If a previous run left bad state:

```bash
rm -rf .venv/fake_home_codex
```

The generator recreates `~/.codex/config.toml` and `~/.codex/auth.json` on the next invocation.

---

## See Also

- [Gemini CLI Evaluation Guide](./gemini_cli_agent_testing.md) — sister doc, shares most concepts
- [Claude Code Evaluation Guide](./claude_code_agent_testing.md) — sister doc, shares most concepts
- [Codex CLI source / docs](https://github.com/openai/codex) — official CLI reference
- [Model Context Protocol (MCP)](https://modelcontextprotocol.io/) — protocol used by tool servers
- [CodexCliGenerator implementation](../evalbench/generators/models/codex_cli.py)
