# Gemini CLI Evaluation Guide

This guide covers how to use EvalBench for evaluating Gemini CLI agent workflows using **MCP Servers**, **Extensions**, and **Skills**. It includes configuration reference, evaluation dataset format, scoring metrics, and step-by-step instructions for running evaluations.

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
- [Tool Paradigms](#tool-paradigms)
  - [MCP Servers](#mcp-servers)
  - [Extensions](#extensions)
  - [Skills](#skills)
  - [Fake MCP Servers (Testing)](#fake-mcp-servers-testing)
- [Scorers](#scorers)
- [Reporting](#reporting)
- [End-to-End Examples](#end-to-end-examples)
- [Troubleshooting](#troubleshooting)

---

## Overview

EvalBench's Gemini CLI integration enables automated, multi-turn evaluation of agentic AI workflows. The Gemini CLI acts as the orchestrator that connects to various tool backends—MCP servers, extensions, or skills—and executes scenarios defined in an evaluation dataset. A **simulated user** powered by an LLM drives multi-turn conversations following a conversation plan, allowing realistic testing without human interaction.

### Key Capabilities

- **Multi-turn evaluation** with LLM-powered simulated users
- **Three tool paradigms**: MCP servers, Gemini CLI extensions, and skills
- **Fake MCP server support** for deterministic, offline testing
- **8 built-in scorers** covering correctness, efficiency, and behavior quality
- **CSV and BigQuery reporting**

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
│                                │  │    GeminiCliGenerator     │   │  │
│  ┌──────────────┐              │  │  ┌─────────┐ ┌─────────┐ │   │  │
│  │ Model Config │──────────────│─▶│  │MCP/Ext/ │ │Simulated│ │   │  │
│  │ (YAML)       │              │  │  │Skills   │ │User     │ │   │  │
│  └──────────────┘              │  │  └─────────┘ └─────────┘ │   │  │
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
2. The **AgentOrchestrator** loads the `geminicli` evaluator.
3. For each scenario in the evalset, the **AgentEvaluator** runs a multi-turn conversation loop:
   - Sends the starting prompt to Gemini CLI
   - A **SimulatedUser** (LLM) generates realistic follow-up responses based on the conversation plan
   - Tools are accumulated across turns
   - Conversation continues until `max_turns` is reached or the simulated user sends `TERMINATE`
4. Results are scored by all configured **scorers**.
5. Reports are written to CSV and/or BigQuery.

---

## Prerequisites

1. **Python 3.10+** and project dependencies installed:
   ```bash
   cd evalbench
   pip install -r requirements.txt
   ```

2. **Node.js and npm** (for Gemini CLI execution)

3. **GCP Authentication** (for Vertex AI models and MCP servers):
   ```bash
   gcloud auth application-default login
   ```

4. **Environment Variables**:
   ```bash
   export EVAL_GCP_PROJECT_ID=your_project_id
   export EVAL_GCP_PROJECT_REGION=us-central1
   ```

---

## Quick Start

### 1. Set the run configuration

```bash
# For MCP Server evaluation:
export EVAL_CONFIG=datasets/gemini-cli-tools/example_run_config.yaml

# For Skills evaluation:
export EVAL_CONFIG=datasets/gemini-cli-tools/example_run_skills_config.yaml

# For Fake MCP (offline testing):
export EVAL_CONFIG=datasets/gemini-cli-tools/example_run_fake_config.yaml
```

### 2. Run the evaluation

```bash
./evalbench/run.sh
```

This executes all scenarios, runs scorers, and writes results to the `results/` directory.

---

## Configuration Reference

Three YAML/JSON files work together to define an evaluation run:

### 1. Run Configuration

The top-level config that ties everything together. For Gemini CLI, set `orchestrator: geminicli` and `dataset_format: gemini-cli-format`.

| Key | Required | Description |
|-----|----------|-------------|
| `dataset_config` | Yes | Path to the evalset JSON file |
| `dataset_format` | Yes | Must be `gemini-cli-format` |
| `orchestrator` | Yes | Must be `geminicli` |
| `model_config` | Yes | Path to the Gemini CLI model config YAML |
| `simulated_user_model_config` | Yes | Path to the model config for the simulated user LLM |
| `scorers` | Yes | Dictionary of scorer configurations (see [Scorers](#scorers)) |
| `reporting` | Optional | CSV and/or BigQuery output options |

**Example** ([example_run_config.yaml](/datasets/gemini-cli-tools/example_run_config.yaml)):

```yaml
############################################################
### Dataset / Eval Items
############################################################
dataset_config: datasets/gemini-cli-tools/gemini-cli.evalset.json
dataset_format: gemini-cli-format

# Orchestrator Configuration
orchestrator: geminicli
model_config: datasets/model_configs/gemini_cli_model.yaml
simulated_user_model_config: datasets/model_configs/gemini_2.5_pro_model.yaml

############################################################
### Scorer Related Configs
############################################################
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

############################################################
### Reporting Related Configs
############################################################
reporting:
  csv:
    output_directory: 'results'
```

---

### 2. Model Configuration

The model config defines the Gemini CLI version, environment, and the tool setup (MCP servers, extensions, or skills). This is the **critical file** that determines which tool paradigm is used.

#### Common Fields

| Key | Required | Description |
|-----|----------|-------------|
| `gemini_cli_version` | Yes | NPM package specifier for Gemini CLI (e.g., `@google/gemini-cli@0.25.1`) |
| `generator` | Yes | Must be `gemini_cli` |
| `env` | Optional | Environment variables passed to the CLI process |
| `setup` | Optional | Tool setup block containing `mcp_servers`, `extensions`, `skills`, or `fake_mcp_servers` |

#### Environment Variables

| Variable | Description |
|----------|-------------|
| `GOOGLE_CLOUD_PROJECT` | GCP project for API calls |
| `GOOGLE_CLOUD_LOCATION` | GCP region |
| `GOOGLE_GENAI_USE_VERTEXAI` | Set to `"true"` to use Vertex AI |
| `GEMINI_API_MODEL` | Override the model used by Gemini CLI |
| `GEMINI_MODEL` | Alternative model override |

---

### 3. Evaluation Dataset (Evalset)

The evalset JSON file defines the test scenarios. Each scenario represents an agentic user journey.

#### Evalset Structure

```json
{
  "scenarios": [
    {
      "id": "unique-scenario-id",
      "starting_prompt": "The initial user message",
      "conversation_plan": "Instructions for the simulated user...",
      "expected_trajectory": ["tool_1", "tool_2"],
      "env": {
        "GOOGLE_CLOUD_PROJECT": "my-project"
      },
      "kind": "tools",
      "max_turns": 6
    }
  ]
}
```

#### Scenario Fields

| Field | Required | Description |
|-------|----------|-------------|
| `id` | Yes | Unique identifier for the scenario |
| `starting_prompt` | Yes | The first user message sent to Gemini CLI |
| `conversation_plan` | Yes | Natural language instructions that guide the simulated user's behavior across turns. This defines the goals, expected information to provide, and how to react to agent responses. |
| `expected_trajectory` | Yes | Ordered list of tool names the agent is expected to call. Used by `trajectory_matcher` scorer. |
| `env` | Optional | Per-scenario environment variables (merged with model config env) |
| `kind` | Optional | Category label (e.g., `"tools"`) |
| `max_turns` | Yes | Maximum number of conversation turns before the evaluation stops |

#### Writing Good Conversation Plans

The `conversation_plan` is a critical part of each scenario. It instructs the simulated user LLM how to behave. Best practices:

1. **Be specific about the goal**: Clearly state what the user wants to accomplish.
2. **Provide concrete values**: Include specific names, values, and parameters the simulated user should provide when asked.
3. **Handle ambiguity intentionally**: Some scenarios test the agent's ability to handle vague requests (e.g., `"I need a database."`).
4. **Include decision points**: Tell the simulated user how to respond to agent confirmations or questions.
5. **Define the project context**: Always specify the GCP project and relevant details.

**Example — Ambiguous Multi-turn Scenario:**
```json
{
  "id": "csql-create-ambiguous-multiturn-01",
  "starting_prompt": "I need a database.",
  "conversation_plan": "The user starts with a vague request. You want to CREATE a NEW Cloud SQL instance named 'my-pg-app'. If the agent offers to create one, say YES. When asked for details, provide 'my-pg-app' as the instance name and 'user_data' as the database name. Never claim to have an existing instance. The goal is for the agent to eventually create the database 'user_data' inside 'my-pg-app' in astana-evaluation project.",
  "expected_trajectory": ["list_instances", "create_instance", "create_database"],
  "env": {
    "GOOGLE_CLOUD_PROJECT": "astana-evaluation"
  },
  "kind": "tools",
  "max_turns": 6
}
```

---

## Tool Paradigms

EvalBench supports three distinct tool paradigms for Gemini CLI, each configured in the `setup` section of the model config YAML. You can also use **fake MCP servers** for deterministic, offline testing.

### MCP Servers

MCP (Model Context Protocol) servers expose tools via a standardized protocol. These can be remote HTTP-based APIs (like Google Cloud managed MCP servers) or local stdio-based servers.

#### Configuration

In the model config, under `setup.mcp_servers`:

```yaml
setup:
  mcp_servers:
    "server-name":
      # For HTTP-based MCP servers:
      httpUrl: "https://example.googleapis.com/mcp"
      authProviderType: google_credentials
      oauth:
        scopes:
        - https://www.googleapis.com/auth/cloud-platform
      headers:
        X-Goog-User-Project: my-project
        
      # For stdio-based MCP servers:
      # command: "python"
      # args:
      #   - "path/to/server.py"
```

#### How It Works

1. **Setup Phase**: `GeminiCliGenerator._setup_mcp_servers()` writes the MCP server configuration into `settings.json` in a sandboxed fake home directory (`.venv/fake_home/.gemini/settings.json`).
2. **Verification**: By default, EvalBench verifies each MCP server by running Gemini CLI and asking it to list loaded tools. If no MCP-specific tools are found, the run fails with a `RuntimeError`. This ensures the server is reachable before evaluation begins.
3. **Execution**: During evaluation, Gemini CLI connects to the configured MCP server(s) and uses the exposed tools to complete tasks.

#### Example: Cloud SQL Managed MCP Server

```yaml
# datasets/model_configs/gemini_cli_model.yaml
gemini_cli_version: "@google/gemini-cli@0.25.1"
generator: gemini_cli
env:
  GOOGLE_CLOUD_PROJECT: "my-project"
  GOOGLE_CLOUD_LOCATION: "us-central1"
  GOOGLE_GENAI_USE_VERTEXAI: "true"
setup:
  mcp_servers:
    "cloud-sql":
      httpUrl: "https://sqladmin.googleapis.com/mcp"
      authProviderType: google_credentials
      oauth:
        scopes:
        - https://www.googleapis.com/auth/cloud-platform
      headers:
        X-Goog-User-Project: my-project
```

> **Note**: Any valid MCP server configuration that Gemini CLI accepts can be used here. The `setup.mcp_servers` block is written directly into the Gemini CLI settings file.

---

### Extensions

Extensions are GitHub-hosted Gemini CLI plugins that provide additional tools. EvalBench can install, configure, and manage extensions automatically.

#### Configuration

In the model config, under `setup.extensions`:

```yaml
setup:
  extensions:
    "https://github.com/org/extension-repo":
      settings:
        SETTING_KEY_1: "value1"
        SETTING_KEY_2: "value2"
```

#### How It Works

1. **Installation**: `GeminiCliGenerator._install_extensions()` calls `gemini extensions install <url> --consent` via the CLI.
2. **Idempotent Management**: Before installing, EvalBench lists current extensions, removes any that are no longer in the config, and skips already-installed ones.
3. **Headless Compatibility**: Extension manifests with `"sensitive": true` are automatically patched to `"sensitive": false` to avoid keychain requirements in CI/headless environments.
4. **Settings**: Extension-specific environment variables (from `settings`) are passed during installation and at CLI runtime.

#### Example: Cloud SQL PostgreSQL Extension

```yaml
# Model config with extensions
gemini_cli_version: "@google/gemini-cli@0.25.1"
generator: gemini_cli
env:
  GOOGLE_CLOUD_PROJECT: "my-project"
  GOOGLE_CLOUD_LOCATION: "us-central1"
  GOOGLE_GENAI_USE_VERTEXAI: "true"
setup:
  extensions:
    "https://github.com/gemini-cli-extensions/cloud-sql-postgresql":
      settings:
        CLOUD_SQL_POSTGRES_PROJECT: "my-project"
        CLOUD_SQL_POSTGRES_INSTANCE: "my-instance"
        CLOUD_SQL_POSTGRES_REGION: "us-central1"
        CLOUD_SQL_POSTGRES_DATABASE: "mydb"
        CLOUD_SQL_POSTGRES_USER: "app"
        CLOUD_SQL_POSTGRES_PASSWORD: "secret"
        CLOUD_SQL_POSTGRES_IP_TYPE: "PUBLIC"
```

---

### Skills

Skills are local Gemini CLI skill packages that can be linked, installed, enabled, disabled, or uninstalled.

#### Configuration

In the model config, under `setup.skills`:

```yaml
setup:
  skills:
    # Link a skill from a local path
    - action: link
      path: "/path/to/skill/directory"
    
    # Install a skill by name or path
    - action: install
      name: "skill-name"
    # or
    - action: install
      path: "/path/to/skill"

    # Enable/Disable a skill
    - action: enable
      name: "skill-name"
    - action: disable
      name: "skill-name"

    # Uninstall a skill
    - action: uninstall
      name: "skill-name"
```

You can also specify skills by name only (as strings), which copies them from `~/.gemini/skills/`:

```yaml
setup:
  skills:
    - "my-existing-skill"
```

#### Supported Actions

| Action | Required Fields | Description |
|--------|----------------|-------------|
| `link` | `path` | Creates a symlink to a local skill directory. Runs `gemini skills link <path> --consent`. |
| `install` | `name` or `path` | Installs a skill from a registry or path. Runs `gemini skills install <target> --consent`. |
| `enable` | `name` | Enables a previously installed skill. |
| `disable` | `name` | Disables a skill without uninstalling it. |
| `uninstall` | `name` | Removes a skill. |

#### How It Works

1. **String-based skills**: If a skill is specified as a plain string, EvalBench copies it from `~/.gemini/skills/<name>` to the sandboxed fake home.
2. **Action-based skills**: If specified as a dict with an `action`, EvalBench runs the appropriate `gemini skills <action>` CLI command.
3. **Sandboxing**: All skills operate within a fake home directory (`.venv/fake_home/`) to isolate the evaluation environment.

#### Example: Cloud SQL Admin Skill

```yaml
# datasets/model_configs/gemini_cli_skills_model.yaml
gemini_cli_version: "@google/gemini-cli@0.31.0"
generator: gemini_cli
env:
  GOOGLE_CLOUD_PROJECT: "my-project"
  GOOGLE_CLOUD_LOCATION: "us-central1"
  GOOGLE_GENAI_USE_VERTEXAI: "true"
  GEMINI_API_MODEL: "gemini-2.5-flash"
  GEMINI_MODEL: "gemini-2.5-flash"
setup:
  skills:
    - action: link
      path: "/path/to/cloud-sql-postgresql/skills/cloudsql-postgres-admin"
```

---

### Fake MCP Servers 

Fake MCP servers let you test your evalset and pipeline with deterministic, hardcoded tool responses—no real API calls needed.

#### Configuration

Fake MCP servers are defined in **two parts**: the server process definition in `setup.fake_mcp_servers`, and the tool definitions in the top-level `fake_mcp_tools` section of the model config.

**Server definition:**
```yaml
setup:
  fake_mcp_servers:
    "server-name":
      command: "python"
      args:
        - "evalbench/util/fake_mcp_server.py"
        - "--server-name"
        - "server-name"
        - "--config"
        - "path/to/this_model_config.yaml"
```

**Tool definitions:**
```yaml
fake_mcp_tools:
  "server-name":
    - name: tool_name
      description: "What this tool does"
      parameters:
        type: object
        properties:
          param1:
            type: string
            description: "Description of param1"
        required: ["param1"]
      response:
        status: "success"
        message: "Hardcoded response"
```

#### How It Works

1. EvalBench starts `fake_mcp_server.py` as a stdio-based MCP server.
2. The server reads tool definitions from the YAML config's `fake_mcp_tools` section.
3. When called, each tool returns its hardcoded `response` (or a default success response containing the tool name and arguments).
4. Tool verification is **skipped** for fake servers (`verify_tools=False`).

#### Tool Response Types

- **Success**: Return a success message
  ```yaml
  response:
    status: "success"
    message: "Instance created successfully"
  ```
- **Failure**: Return an error
  ```yaml
  response:
    status: "failure"
    error:
      code: 404
      message: "Instance not found or permission denied"
  ```
- **Default**: If no `response` is specified, returns `{"status": "success", "tool": "<name>", "args": {<arguments>}}`

#### Example: Fake Cloud SQL MCP

```yaml
# datasets/model_configs/gemini_cli_fake_model.yaml
gemini_cli_version: "@google/gemini-cli@0.25.1"
generator: gemini_cli
env:
  GOOGLE_CLOUD_PROJECT: "astana-evaluation"
  GOOGLE_CLOUD_LOCATION: "us-central1"
  GOOGLE_GENAI_USE_VERTEXAI: "true"
setup:
  fake_mcp_servers:
    "cloud-sql":
      command: "python"
      args:
        - "evalbench/util/fake_mcp_server.py"
        - "--server-name"
        - "cloud-sql"
        - "--config"
        - "datasets/model_configs/gemini_cli_fake_model.yaml"

fake_mcp_tools:
  "cloud-sql":
    - name: create_instance
      description: "Creates a Cloud SQL instance"
      parameters:
        type: object
        properties:
          project_id:
            type: string
          instance_name:
            type: string
        required: ["project_id", "instance_name"]
      response:
        status: "success"
        message: "Instance created successfully"
    - name: get_instance
      description: "Gets details of a Cloud SQL instance"
      parameters:
        type: object
        properties:
          project_id:
            type: string
          instance_name:
            type: string
        required: ["project_id", "instance_name"]
      response:
        status: "failure"
        error:
          code: 404
          message: "Instance not found or permission denied"
```

---

## Scorers

EvalBench provides **8 scorers** for Gemini CLI evaluations. Scorers are configured in the `scorers` section of the run config.

### LLM-Based Scorers

These require a `model_config` pointing to an LLM for evaluation:

| Scorer | Score Range | Description |
|--------|------------|-------------|
| `goal_completion` | 0–100 | Uses an LLM to evaluate whether the agent accomplished the conversation plan's intent. Returns `100` for PASS, `0` for FAIL. |
| `behavioral_metrics` | 0–100 | Evaluates hallucination rate and clarification rate in a single LLM pass. Starts at 100 and penalizes: **-50 per hallucination**, **-20 per unnecessary clarification**. |
| `parameter_analysis` | 100 (qualitative) | Uses an LLM to provide qualitative feedback on tool parameters used. Always scores 100; the value is in the textual explanation. |

### Deterministic Scorers

These require no additional model:

| Scorer | Score Range | Description |
|--------|------------|-------------|
| `trajectory_matcher` | 0–100 | Compares expected vs. actual tool usage. Uses **Jaccard Similarity** by default (set-based, order-insensitive). Set `enforce_order: true` for **Levenshtein distance** (order-sensitive). |
| `turn_count` | Count | Reports the number of conversation turns the agent took. Lower is generally better. |
| `end_to_end_latency` | Milliseconds | Total latency = model API latency + tool execution latency. |
| `tool_call_latency` | Milliseconds | Sum of all tool execution durations across all turns. |
| `token_consumption` | Count | Total tokens consumed (input + output) across all turns. |

### Scorer Configuration Example

```yaml
scorers:
  # Deterministic scorers (no model needed)
  trajectory_matcher: {}
  # trajectory_matcher:
  #   enforce_order: true  # Use Levenshtein for ordered matching
  turn_count: {}
  end_to_end_latency: {}
  tool_call_latency: {}
  token_consumption: {}

  # LLM-based scorers (require model_config)
  goal_completion:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
  behavioral_metrics:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
  parameter_analysis:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
```

---

## Reporting

### CSV Reporting

Results are output as CSV files in the specified directory:

```yaml
reporting:
  csv:
    output_directory: 'results'
```

### BigQuery Reporting

For centralized result storage and dashboarding:

```yaml
reporting:
  bigquery:
    gcp_project_id: my-gcp-project
```

---

## End-to-End Examples

### Example 1: Evaluate MCP Server (Real API)

**Goal**: Test Cloud SQL management via the managed MCP server.

1. **Create model config** (`model_configs/gemini_cli_model.yaml`):
   ```yaml
   gemini_cli_version: "@google/gemini-cli@0.25.1"
   generator: gemini_cli
   env:
     GOOGLE_CLOUD_PROJECT: "my-project"
     GOOGLE_CLOUD_LOCATION: "us-central1"
     GOOGLE_GENAI_USE_VERTEXAI: "true"
   setup:
     mcp_servers:
       "cloud-sql":
         httpUrl: "https://sqladmin.googleapis.com/mcp"
         authProviderType: google_credentials
         oauth:
           scopes:
           - https://www.googleapis.com/auth/cloud-platform
         headers:
           X-Goog-User-Project: my-project
   ```

2. **Create evalset** (`my-evalset.json`):
   ```json
   {
     "scenarios": [
       {
         "id": "list-and-inspect-01",
         "starting_prompt": "list all instances in project my-project",
         "conversation_plan": "Ask the agent to list instances. Once listed, get details of the 'prod-db' instance and verify it is RUNNABLE.",
         "expected_trajectory": ["list_instances", "get_instance"],
         "env": { "GOOGLE_CLOUD_PROJECT": "my-project" },
         "kind": "tools",
         "max_turns": 3
       }
     ]
   }
   ```

3. **Create run config** (`my-run-config.yaml`):
   ```yaml
   dataset_config: my-evalset.json
   dataset_format: gemini-cli-format
   orchestrator: geminicli
   model_config: model_configs/gemini_cli_model.yaml
   simulated_user_model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
   scorers:
     trajectory_matcher: {}
     goal_completion:
       model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
     turn_count: {}
   reporting:
     csv:
       output_directory: 'results'
   ```

4. **Run**:
   ```bash
   export EVAL_CONFIG=my-run-config.yaml
   ./evalbench/run.sh
   ```

### Example 2: Evaluate Skills

**Goal**: Test Cloud SQL management via a linked skill.

1. **Create model config** (`model_configs/gemini_cli_skills_model.yaml`):
   ```yaml
   gemini_cli_version: "@google/gemini-cli@0.31.0"
   generator: gemini_cli
   env:
     GOOGLE_CLOUD_PROJECT: "my-project"
     GOOGLE_CLOUD_LOCATION: "us-central1"
     GOOGLE_GENAI_USE_VERTEXAI: "true"
     GEMINI_API_MODEL: "gemini-2.5-flash"
     GEMINI_MODEL: "gemini-2.5-flash"
   setup:
     skills:
       - action: link
         path: "/path/to/skills/cloudsql-postgres-admin"
   ```

2. **Use the same evalset** — the scenarios are tool-paradigm agnostic.

3. **Create run config** pointing to the skills model config.

4. **Run**:
   ```bash
   export EVAL_CONFIG=my-skills-run-config.yaml
   ./evalbench/run.sh
   ```

### Example 3: Offline Testing with Fake MCP

**Goal**: Validate the pipeline without making real API calls.

1. **Create model config** with fake tools (see [Fake MCP Servers](#fake-mcp-servers-testing) section for full example).

2. **Create a simple evalset** targeting the fake tools:
   ```json
   {
     "scenarios": [
       {
         "id": "fake-create-success",
         "starting_prompt": "Create a new Cloud SQL instance named 'test-db' in project 'my-project'.",
         "conversation_plan": "All details are in the prompt. The agent should call create_instance and report success.",
         "expected_trajectory": ["create_instance"],
         "env": { "GOOGLE_CLOUD_PROJECT": "my-project" },
         "kind": "tools",
         "max_turns": 3
       }
     ]
   }
   ```

3. **Run**:
   ```bash
   export EVAL_CONFIG=datasets/gemini-cli-tools/example_run_fake_config.yaml
   ./evalbench/run.sh
   ```

---

## Troubleshooting

### MCP Server Verification Fails

```
RuntimeError: MCP Server 'cloud-sql' failed verification
```

- Ensure the MCP server URL is correct and accessible.
- Check that `gcloud auth application-default login` has been run.
- Verify the OAuth scopes and project ID in the headers.
- Check network/firewall rules if the server is remote.

### Extension Installation Fails

- Ensure `npm` is installed and accessible.
- Check the extension GitHub URL is correct and accessible.
- Look at logs for "keychain" errors — these are auto-patched, but the extension may have other issues.
- Run `npm exec --yes @google/gemini-cli -- extensions list` manually to debug.

### Skill Linking Fails

- Verify the skill path exists and contains a valid skill structure.
- Ensure the Gemini CLI version supports the `skills link` command.
- Check that the path is an absolute path.

### Empty or Missing Results

- Confirm `dataset_format` is set to `gemini-cli-format`.
- Check that the evalset JSON has the correct `"scenarios"` key.
- Verify the `model_config` path is correct relative to the repo root.

### NPM Authentication Issues

- EvalBench auto-configures NPM auth tokens for private registries using `gcloud auth print-access-token`.
- If this fails, run `gcloud auth login` and retry.

---