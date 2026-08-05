# Antigravity (agy) CLI Evaluation Guide

This guide covers how to use EvalBench for evaluating [Antigravity CLI](https://antigravity.google/product/antigravity-cli) (`agy`)
agent workflows using **MCP Servers** and **Skills**. It mirrors the structure
of [`gemini_cli_agent_testing.md`](gemini_cli_agent_testing.md) and only calls
out where the two harnesses differ.

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
- [Differences vs. Gemini CLI](#differences-vs-gemini-cli)
- [Scorers](#scorers)
- [Reporting](#reporting)
- [Troubleshooting](#troubleshooting)

---

## Overview

EvalBench's agy CLI integration enables automated, multi-turn evaluation of
agentic workflows that run on the Antigravity CLI binary. Same evaluator,
same scorers, same evalset format as the Gemini CLI guide -- the generator
just shells out to the `agy` binary instead of `npm exec @google/gemini-cli`.

### Key Capabilities

- **Multi-turn evaluation** with LLM-powered simulated users
- **Two tool paradigms** today: MCP servers and skills (agy does not expose a
  Gemini-CLI-style extension manager)
- **Fake MCP server support** for deterministic, offline testing
- **Same 8 built-in scorers** as Gemini CLI
- **CSV and BigQuery reporting**

---

## Architecture

Identical to the Gemini CLI flow; only the generator class changes:

```
Run Config -> AgentOrchestrator -> AgentEvaluator -> AgyCliGenerator -> agy
                                                       |
                                                       v
                                              MCP servers / skills
```

The `orchestrator: agent` keyword in your run config selects the `AgentOrchestrator`, while the concrete CLI generator (`agy_cli`) is chosen via the `generator` field in your `model_config`.

---

## Prerequisites

1. **Python 3.10+** and project dependencies installed using `uv`:
   ```bash
   cd evalbench
   uv sync
   ```

2. **GCP Authentication** (ADC -- agy's own backend, plus outbound credentials
   for Google-auth MCP servers; see [Authentication](#authentication)):
   ```bash
   gcloud auth application-default login
   ```

3. **Environment Variables**:
   ```bash
   export EVAL_GCP_PROJECT_ID=your_project_id
   export EVAL_GCP_PROJECT_REGION=global
   ```

> [!NOTE]
> **You do not need `agy` on the host.** The harness installs its own copy per
> session into `<fake_home>/.local/bin` and always launches that one, never the
> host `PATH` binary. Install it on the host only to run `agy models` yourself,
> pointing `--dir` at a directory on your `PATH`:
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

For agy CLI, set `orchestrator: agent` (the modern agent-CLI keyword,
shared with `claude_code` and `codex_cli`) and
`dataset_format: agent-format`. The legacy `geminicli` /
`gemini-cli-format` values still work -- both route to
`AgentOrchestrator` -- but the `agent*` names are the right ones for
non-gemini CLIs.

| Key | Required | Description |
|-----|----------|-------------|
| `dataset_config` | Yes | Path to the evalset JSON file |
| `dataset_format` | Yes | `agent-format` (recommended) or the legacy `gemini-cli-format` |
| `orchestrator` | Yes | `agent` (recommended) or the legacy `geminicli` |
| `model_config` | Yes | Path to the agy CLI model config YAML |
| `simulated_user_model_config` | Yes | Path to the model config for the simulated user LLM |
| `scorers` | Yes | Dictionary of scorer configurations |
| `reporting` | Yes | CSV and/or BigQuery output options |

**Example** ([example_run_config.yaml](/datasets/agy-cli-tools/example_run_config.yaml)):

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

| Key | Required | Description |
|-----|----------|-------------|
| `generator` | Yes | Must be `agy_cli` |
| `model` | Optional | Model for the run -- the UI label listed by `agy models`, e.g. `"Gemini 3.1 Pro (Low)"`. An unrecognized value fails the run. Omit to use agy's default. |
| `timeout` | Optional | Timeout duration string, e.g. `"20m"`. Passed via the `--print-timeout` flag. If omitted, defaults to agy's internal default (5 minutes). |
| `env` | Optional | Environment variables passed to the CLI process |
| `setup` | Optional | Tool setup block containing `mcp_servers`, `skills`, or `fake_mcp_servers` |

> [!IMPORTANT]
> **Explicit Project Configuration Required:** agy does not read GCP project details from the host environment. You **must** explicitly set `GOOGLE_CLOUD_PROJECT` and `GOOGLE_CLOUD_LOCATION` in the `env` block of your model config. If omitted, agy will return empty responses and fail to make tool calls, even if those variables are exported in your shell.

---

### 3. Evaluation Dataset (Evalset)

Same format as Gemini CLI. See the [Gemini guide's evalset section](gemini_cli_agent_testing.md#3-evaluation-dataset-evalset)
for full field reference; the same `expected_trajectory` canonical form
(`<server>__<tool>`) applies. The `agy-cli-tools/` directory ships copies of
the Gemini Cloud SQL evalsets so the two harnesses score against an
identical baseline.

---

## Authentication

agy authenticates from **gcloud ADC** (agy >= 1.1.10), which the harness opts
into by setting `AGY_ADC_AUTH` on every run. The same ADC supplies outbound
credentials for `authProviderType: google_credentials` MCP servers, so one
`gcloud auth application-default login` covers both, and runs are fully
non-interactive.

agy's own interactive OAuth login is not used: the harness never mirrors a host
token into the eval sandbox. One consequence is the entitled **model set** --
ADC does not reach `Gemini 3.1 Pro (High)`. Run `agy models` under ADC to see
which labels the `model` key can use.

---

## Tool Paradigms

### MCP Servers

Configured under `setup.mcp_servers` in the model config. EvalBench writes
the block under the `mcpServers` key of a sandboxed
`<fake_home>/.gemini/config/mcp_config.json` (a separate file from
`settings.json`) and lets agy pick it up at startup.

> [!NOTE]
> **Use `httpUrl` for the HTTP endpoint**, same as the other harnesses -- EvalBench
> translates it to agy's native `serverUrl`. `authProviderType`, `oauth.scopes`,
> and `headers` are native agy fields, so Google auth works without
> Bearer-header injection (unlike `claude_code`).

The harness pre-verifies attach: at setup it runs
a short probe, then confirms each configured server discovered
tools by checking that agy wrote the expected per-tool schema files to
the local app data cache. A server that attaches no tools fails the evaluation
with the offending server name rather than silently degrading.

### Skills

> [!NOTE]
> The field is named `setup.skills` for parity with the `claude_code` and
> `codex_cli` harnesses, which use the same key. For agy each entry is
> installed as a **plugin** (`agy plugin install`), and a plugin bundle may
> carry skills *and* its own MCP servers. The separate top-level
> `setup.mcp_servers` block is for attaching a **standalone** MCP server (by
> URL/stdio) that is not packaged in a plugin -- the two are distinct attach
> paths and are configured independently.

Configured under `setup.skills`. Skills are delivered via **plugins**:
`agy plugin install <target>` reads a plugin
manifest (Claude/Gemini/Codex formats), processes any bundled skills,
materializes them under `<HOME>/.gemini/config/plugins/<name>/`, and
records the install in `<HOME>/.gemini/config/import_manifest.json`. There
is no `agy skills` subcommand, and dropping `SKILL.md` folders on disk
registers nothing (`agy plugin list` stays empty). The harness therefore
shells out to `agy plugin install` for every entry. Two input shapes are
supported:

```yaml
setup:
  skills:
    # String form: an install target passed straight to
    # `agy plugin install`. May be a local plugin directory or a git URL
    # (cloned first). `agy plugin install` requires the target to resolve
    # to a directory, so a bare git URL is cloned before install.
    - "/path/to/a/local/plugin"

    # Dict form: same, via an explicit target. Git URLs (scheme:// or
    # trailing .git) are cloned first, then the clone dir is installed;
    # local paths are installed in place. `url:` is conventional; `path:`
    # is accepted as a synonym. Append `#<branch-or-tag>` to a git URL to
    # pin a version -- the clone uses `git clone --branch`, which resolves
    # branch and tag names only, not raw commit SHAs.
    - action: install_from_repo
      url: "https://github.com/gemini-cli-extensions/cloud-sql-postgresql.git#v1.2.3"
```

> [!NOTE]
> A `plugin@marketplace` spec is not a reliable target (unlike `claude_code`/
> `gemini_cli`); use a git URL or local directory. Legacy dict actions
> (`link`, `install`, `enable`, `disable`, `uninstall`) that the gemini-cli
> generator supports are **not** supported here either -- use a string target
> or `install_from_repo`. Unsupported entries are logged and skipped.

### Fake MCP Servers (Testing)

Identical setup to Gemini CLI -- a stdio MCP server defined in
`setup.fake_mcp_servers` with tool definitions in the top-level
`fake_mcp_tools` block. See
[`datasets/model_configs/agy_cli_fake_model.yaml`](../datasets/model_configs/agy_cli_fake_model.yaml)
for a working example.

---

## Differences vs. Gemini CLI

| Area | Gemini CLI | Antigravity (agy) |
|------|-----------|--------------------|
| Install | `npm install -g @google/gemini-cli@<ver>` | `curl install.sh \| sh -- --dir <bin>` |
| Version pinning | NPM specifier in `gemini_cli_version` | No pinning mechanism; the latest binary is installed dynamically at runtime. |
| Invocation | `npm exec --yes @google/gemini-cli@<ver> -- ...` | `agy ...` (bare binary) |
| Non-interactive flag | `--yolo` / `--prompt` | `--dangerously-skip-permissions` and `-p` (alias `--print`) |
| Output format | `--output-format stream-json` (NDJSON on stdout) | `--output-format stream-json` (NDJSON on stdout); the harness parses this event stream (see below) |
| Session resume | `--resume <id>` | `--continue` (most recent in cwd) or `--conversation <uuid>` |
| Settings dir (`appDataDir`) | `~/.gemini/` | `~/.gemini/antigravity-cli/` |
| Settings file | `~/.gemini/settings.json` | `~/.gemini/antigravity-cli/settings.json` |
| Skills dir | `~/.gemini/skills/<name>/SKILL.md` | `~/.gemini/config/plugins/<name>/` (materialized by `agy plugin install`) |
| Skill management | `gemini skills <link\|install\|enable\|...>` subcommands | `agy plugin install <target>` (plugin manifests carry skills); no `agy skills` subcommand |
| Extensions | Supported via `setup.extensions` | Not modeled; drop the block |
| MCP config location | `mcpServers` in `settings.json` | `mcpServers` in a separate `~/.gemini/config/mcp_config.json` |
| MCP HTTP transport field | `httpUrl` | `httpUrl`, translated by the harness to agy's native `serverUrl` (`url` also works natively) |
| MCP tool name format | `mcp_<server>_<tool>` (single underscore) | No per-tool functions -- every MCP call goes through a single native `call_mcp_tool` wrapper whose args carry `ServerName`/`ToolName`/`Arguments`; the harness unwraps it to the canonical `<server>__<tool>` |
| Model selection | `GEMINI_API_MODEL` / `GEMINI_MODEL` env var | `--model` flag; UI label as listed by `agy models` |
| Auth | NPM auth token via `gcloud auth print-access-token` plus ADC | ADC via `AGY_ADC_AUTH`, covering both the backend and MCP servers |
| Token-usage stats | Reported per request | Exposed via the stream-json `result` event's `usage` block (input/output/thinking/total tokens) |

### Tool-call extraction

The harness runs agy with `--output-format stream-json` and parses the event
stream (`init`, `step_update` x N, `result`) from stdout. Each tool call is a
`step_update` whose `ACTIVE` -> `DONE`/`ERROR` states give success/failure
directly; `call_mcp_tool` invocations are unwrapped into the canonical
`<server>__<tool>` format. The stream is per-invocation, so no turn-slicing is
needed under `--continue`. A run that times out or errors exits non-zero but
still emits a full stream ending in an `ERROR` `result`, so its tool calls are
captured too.

---

## Scorers

Identical to Gemini CLI -- see the
[scorers section of the Gemini guide](gemini_cli_agent_testing.md#scorers).
The `trajectory_matcher` default of dropping native/harness-internal tools
also applies.

---

## Reporting

Identical to Gemini CLI. CSV under `reporting.csv.output_directory`,
BigQuery under `reporting.bigquery.gcp_project_id`.

---

## Troubleshooting

### Run prompts for a login / `authentication timed out`

agy fell back to its interactive login because ADC was unreadable. Run
`gcloud auth application-default login` and re-run; the harness logs a warning
at setup when it finds no ADC file.

### `invalid model selection`

ADC is entitled to a smaller model set than agy's interactive login. Run
`agy models` and use a label it lists.

### MCP Server Doesn't Attach

The harness pre-verifies attach at setup: it runs a
short probe and fails fast if a configured
server discovered no tools. If you hit that error:

- **Check the URL field** (see the MCP Servers callout above): a typo'd or
  unrecognized URL key is accepted silently and exposes zero tools, so it
  looks like a load failure.
- Confirm the block lives under `mcpServers` in
  `<fake_home>/.gemini/config/mcp_config.json` (not `settings.json`) after
  setup runs.
- Confirm agy wrote per-tool schemas to
  `<fake_home>/.gemini/antigravity-cli/mcp/<server>/*.json` -- an empty
  directory means the server failed to attach.
- For Google-auth servers, run `gcloud auth application-default login`
  (the same ADC agy authenticates with, also used for outbound credentials to
  the MCP server).
- Verify OAuth scopes and project ID in the headers.

### Skill Not Picked Up

- Confirm the plugin registered: check that the name appears in
  `<fake_home>/.gemini/config/import_manifest.json` and that
  `<fake_home>/.gemini/config/plugins/<name>/` was materialized after
  setup runs. The setup log line `agy registered plugins: [...]` reports
  what `agy plugin install` recorded.
- If `agy plugin install` failed, the harness logs an `agy plugin install
  '<target>' failed` line with the install command's exit code and stderr --
  read that line for the reason. A bad target is the usual cause: a wrong
  path, an unreachable git URL, or a directory with no valid plugin manifest
  (agy accepts Claude/Gemini/Codex manifest formats, e.g. `plugin.json` or
  `gemini-extension.json`).
- If you have an `action: link` / `enable` / `install` entry in your
  config, drop it -- those gemini-cli-style actions are not supported
  here and are logged-and-skipped. Use a string target or
  `install_from_repo`.

### Empty or Missing Results

- Confirm `dataset_format` is `agent-format` (or the legacy `gemini-cli-format`).
- Verify the `model_config` path is correct relative to the repo root.
- Check that `agy --version` works from the same shell.
