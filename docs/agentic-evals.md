# Agentic Evaluations

EvalBench evaluates **agents** — coding CLIs and data agents that reason across multiple turns, call tools, and act on their environment. Agentic evaluations share the same run config, scorer framework, and reporting as EvalBench's single-turn tasks such as NL2SQL.

Each scenario starts from a prompt and is driven forward by an **LLM-based simulated user** that follows a `conversation_plan` until the goal is met, a terminal state is reached, or `max_turns` is exhausted. Every turn is captured — text, tool calls, parameters, latency, and tokens — and then scored.

**Contents**

- [Supported agents](#supported-agents)
- [Execution model](#execution-model)
- [Sandboxing](#sandboxing)
- [Tool paradigms](#tool-paradigms)
- [Scoring](#scoring)
- [Quick start](#quick-start)

---

## Supported agents

| Agent | Generator | Orchestrator | Guide |
|---|---|---|---|
| Gemini CLI | `gemini_cli` | `agent` / `geminicli` | [Gemini CLI guide](/docs/gemini_cli_agent_testing.md) |
| Claude Code | `claude_code` | `agent` | [Claude Code guide](/docs/claude_code_agent_testing.md) |
| Codex CLI | `codex_cli` | `agent` | [Codex CLI guide](/docs/codex_cli_agent_testing.md) |
| Antigravity (agy) CLI | `agy_cli` | `agent` | [Antigravity CLI guide](/docs/agy_cli_agent_testing.md) |
| Conversational data agents | `dataagent` | `dataagent` / `interact` | [Data agent spec](/docs/dataagent_spec.md) |
| Agent gRPC Proxy | `agent_grpc_proxy` | `agent` | Bidirectional gRPC streaming agent |

The `generator` is set in your [model config](/docs/configs/model-config.md); the `orchestrator` is set in your [run config](/docs/configs/run-config.md), or inferred from `dataset_format`.

---

## Execution model

1. The **run config** ties together the dataset, model config, scorers, and reporting.
2. The **orchestrator** (`agent`) loads the evaluator for the configured generator.
3. For each scenario in the evalset, the **agent evaluator** runs a multi-turn conversation loop:
   - Sends `starting_prompt` to the agent CLI.
   - A **simulated user** — itself an LLM — generates realistic follow-up responses based on `conversation_plan`.
   - Tool calls are accumulated across turns.
   - The conversation continues until `max_turns` is reached or the simulated user emits `TERMINATE`.
4. All configured [scorers](/docs/scorers.md) run against the captured trajectory.
5. Results are written to CSV and/or BigQuery.

Because the simulated user is an LLM following a natural-language plan rather than a fixed script, scenarios exercise realistic ambiguity, clarification, and recovery behavior. Writing a good `conversation_plan` matters as much as writing a good prompt — see [conversation plan guidance](/docs/configs/agentic-dataset-config.md#writing-good-conversation-plans).

---

## Sandboxing

Every run is sandboxed. Agents execute against an isolated fake home directory, so evaluations never contaminate your local machine's CLI settings, and scenarios can run concurrently without interfering with each other.

| Agent | Sandbox home |
|---|---|
| Gemini CLI | `.venv/fake_home/` |
| Claude Code | `.venv/fake_home_claude/` |
| Codex CLI | `.venv/fake_home_codex/` |

Each generator writes its CLI's native configuration (`settings.json`, `config.toml`, `mcp_config.json`, and so on) into that sandbox and invokes the CLI with `HOME` pointed at it. In gRPC service mode, the sandbox is per-session at `/tmp_sessions/<session_id>/fake_home` instead.

If a run leaves bad state behind, deleting the relevant fake home directory is a safe reset.

---

## Tool paradigms

Agents can be evaluated against the tools they are given, regardless of how those tools are wired up. These are configured in the `setup` section of the model config.

| Paradigm | How it works |
|---|---|
| **MCP servers** | Remote HTTP/SSE or local stdio Model Context Protocol servers, mounted into the agent's sandbox. |
| **Plugins** | Plugin bundles installed from a git repo or local directory via each CLI's plugin marketplace. A plugin can carry skills, MCP servers, or both. |
| **Extensions** | GitHub-hosted plugin packages installed idempotently via the CLI. |
| **Skills** | Skill packages installed through each CLI's native mechanism. |
| **Fake MCP** | A deterministic local MCP stub for fast, offline, zero-cost testing of your harness and datasets. |

Fake MCP is the right starting point when you are developing an evalset — it exercises the full pipeline with no network calls, no credentials, and no cost, so you can validate scenario wiring before pointing at a real server.

### A note on plugins vs. skills vs. extensions

These overlap, and each CLI draws the lines differently. Plugins are best understood as a *packaging and delivery* mechanism rather than a separate kind of tool: the harness clones a plugin marketplace, registers it, and enables the target plugin, and whatever that plugin bundles — skills, MCP servers, or both — becomes available to the agent.

| Agent | How plugins work |
|---|---|
| Claude Code | A marketplace repo or local directory (`.claude-plugin/marketplace.json`) is registered as a `directory` source and the target plugin enabled via `enabledPlugins`. Plugins may bundle MCP servers, which Claude Code auto-starts. Entries can name a specific `plugin` and pass `config` values for its `userConfig`. |
| Codex CLI | A plugin repo is cloned into `<fake_home>/.codex/plugins/`, registered in `marketplace.json`, and enabled in `config.toml` as `<plugin>@<marketplace>`. |
| Antigravity (agy) | `agy plugin install <target>` against a local directory or git URL. There is no `agy skills` subcommand — **plugins are the only way to deliver skills**, since plugin manifests carry them. |
| Gemini CLI | Exposed as **extensions** — GitHub-hosted Gemini CLI plugins providing additional tools. |

Exact configuration syntax differs per CLI; see the per-agent guide linked in [Supported agents](#supported-agents).

---

## Scoring

Agentic runs are scored by the trajectory, goal, behavior, and cost scorers documented in the [scorer reference](/docs/scorers.md#agentic-scorers). The most common starting set:

```yaml
scorers:
  trajectory_matcher: {}
  turn_count: {}
  end_to_end_latency: {}
  token_consumption: {}
  goal_completion:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
  behavioral_metrics:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
```

### Stats across trials

Set [`num_trials`](/docs/configs/run-config.md#num_trials-examples) above 1 in the run config to run each scenario more than one time. The job then also writes `run_stats.csv`. The file has one row for each scenario. Only the `csv` reporter writes it.

A trial passes if its `goal_completion` score is at or above `pass_threshold`. The default is 100. To change it, set `pass_threshold` on the scorer to a number from 0 to 100:

```yaml
scorers:
  goal_completion:
    pass_threshold: 80
```

A trial is unscored if it has an agent error in `generated_error` or if the `goal_completion` scorer fails. An unscored trial does not count as a pass or as a failure.

| Column | Description |
| ------ | ----------- |
| `scenario_id` | The scenario `id` from the evalset. It is the `prompt_id` of the result rows. |
| `num_trials` | Number of trials of the scenario that ran. |
| `scored_trials`, `unscored_trials` | Number of trials that were scored, and number that were unscored. |
| `passed_trials` | Number of scored trials that passed. |
| `pass_rate` | `passed_trials / scored_trials`, from 0 to 1. |
| `flakiness` | `4p(1 - p)`, where `p` is the pass rate. It is 0 if all scored trials agree and 1 at 50/50. |
| `flaky` | `True` if some scored trials passed and some failed. |
| `confidence` | The chance, from 0 to 1, that the true pass rate is above 90%. The true pass rate is the pass rate over a very large number of trials. |
| `interval_low`, `interval_high` | The Wilson interval. It is the range that holds the true pass rate with 95% certainty. |
| `score_mean`, `score_std` | Mean and sample standard deviation of the `goal_completion` scores of the scored trials. |
| `status` | `PASS`: every trial was scored and passed. `FAIL`: every trial was scored and at least one failed. `ERROR`: at least one trial was unscored. |
| `<metric>_min`, `<metric>_median`, `<metric>_max` | For each numeric metric, such as `token_consumption`, `end_to_end_latency`, and `turn_count`: the min, median, and max over the scored trials. |
| `<scorer>_pass_rate` | For each other 0 to 100 scorer, such as `trajectory_matcher`: the share of scored trials with a score at or above the `pass_threshold` of that scorer. The default is 100. |

If a scenario has no scored trial, its `status` is `ERROR` and its other stats are empty. Without the `goal_completion` scorer, the pass stats are empty.

With few trials, the `confidence` is low and the interval is wide. For example, 3 passes in 3 trials give a `confidence` of 0.34 and an interval of 0.44 to 1.

The job also adds the stats of the whole suite to `summary.csv`, in these columns. The existing columns do not change.

| Column | Row | Description |
| ------ | --- | ----------- |
| `pass_rate` | `goal_completion` | Passed trials divided by scored trials, over all scenarios. |
| `interval_low`, `interval_high` | `goal_completion` | The Wilson interval of `pass_rate`. |
| `flakiness`, `confidence` | `goal_completion` | The average of the scenario values, over the scenarios with more than one scored trial. |
| `flaky_scenarios` | `goal_completion` | Number of flaky scenarios. |
| `trial_min`, `trial_median`, `trial_max` | Each numeric metric | EvalBench takes the suite average of the metric in each trial. These columns have the min, median, and max of those averages. Only the scenarios with a scored result in every trial count. |

The viewer shows the pass stats on the Dashboard tab of the run, in the section "Stats across trials". The section has the stats of the suite and one row for each scenario, with the flaky scenarios first. A tooltip on each column header gives the definition of the column. A run without `run_stats.csv` has no such section.

---

## Quick start

Define a scenario in an evalset — see the [agentic dataset format](/docs/configs/agentic-dataset-config.md) for the full schema:

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
