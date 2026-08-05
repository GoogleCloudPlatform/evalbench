# EvalBench Documentation

Reference documentation for [EvalBench](/README.md). Start with the [main README](/README.md) for installation and a first run.

## Configuration

Every evaluation is driven by a run config that points at a dataset, a database or agent, a model, and a set of scorers.

| Doc | Contents |
|---|---|
| [Run config](/docs/configs/run-config.md) | The top-level YAML for an evaluation run — dataset, generation, setup/teardown, scorers, reporting. |
| [NL2SQL dataset format](/docs/configs/dataset-config.md) | Evaluation items for single-turn SQL evaluations: prompts, golden SQL, eval queries. |
| [Agentic dataset format](/docs/configs/agentic-dataset-config.md) | Scenario evalsets for multi-turn agent evaluations: prompts, conversation plans, expected trajectories. |
| [Database config](/docs/configs/db-config.md) | Connection details per database and the list of supported dialects. |
| [Model config](/docs/configs/model-config.md) | Model selection and generation settings. |

## Scoring

| Doc | Contents |
|---|---|
| [Scorers](/docs/scorers.md) | Full catalog of every scorer — SQL, agentic, skills, transform tooling, and custom Python scorers — with config options. |
| [Judge tools](/docs/judge_tools.md) | Giving LLM-judged scorers function-calling access instead of single-shot prompting. |
| [Summarizer](/docs/summarizer_documentation.md) | How run summaries are aggregated and the rationale behind the formulas. |

## Agentic evaluations

| Doc | Contents |
|---|---|
| [Agentic evaluations](/docs/agentic-evals.md) | Execution model, sandboxing, and tool paradigms. Start here. |
| [Gemini CLI](/docs/gemini_cli_agent_testing.md) | Setup and configuration for evaluating Gemini CLI. |
| [Claude Code](/docs/claude_code_agent_testing.md) | Setup and configuration for evaluating Claude Code. |
| [Codex CLI](/docs/codex_cli_agent_testing.md) | Setup and configuration for evaluating Codex CLI. |
| [Antigravity CLI](/docs/agy_cli_agent_testing.md) | Setup and configuration for evaluating the Antigravity (agy) CLI. |
| [Data agent spec](/docs/dataagent_spec.md) | ADKDataAgent support — multi-turn database agents with clarification turns. |

## Examples

Runnable notebooks in [docs/examples/](/docs/examples/):

- [SQLite example](/docs/examples/sqlite_example.ipynb) — the quickest end-to-end run, no cloud resources needed.
- [GCP Cloud SQL example](/docs/examples/GCP_CloudSQL_Example.ipynb)
- [BigQuery hybrid example](/docs/examples/bigquery_hybrid_example.ipynb)

## Project

| Doc | Contents |
|---|---|
| [Contributing](/docs/contributing.md) | How to submit patches and contributions. |
| [Code of conduct](/docs/code-of-conduct.md) | Community guidelines. |
| [Dependency graph](/docs/architecture.md) | External dependency graph, dependency groups by purpose, and supply-chain risk surfacing. |
