---
name: evalbench-cloudsql-report
description: Generate a detailed, accurate MCP-vs-gcloud comparison report from EvalBench Cloud SQL experiment results. Use this when asked to analyze, summarize, or write up the results of the Cloud SQL CUJ experiment (the easy/medium/hard evalsets run in both Google-managed MCP mode and gcloud-only mode) across one or more agents (agy_cli, claude_code, codex, gemini_cli). Reads the per-run CSVs EvalBench writes under results/<job_id>/ and produces a structured Markdown comparison report.
---

# EvalBench Cloud SQL: MCP-vs-gcloud Report

This skill turns raw EvalBench output into a rigorous comparison report between the
**Google-managed MCP server** (`eval_group: mcp`) and the **gcloud-only** baseline
(`eval_group: gcloud-only`) for the Cloud SQL CUJ experiment.

> **Internal documentation:** `<ADD LINK>`
> When this link is filled in, FETCH it first and follow the report structure,
> required sections, terminology, and metric definitions specified there. The
> structure below is the default to use only until the internal doc is provided;
> the internal doc always wins on formatting and required content.

## Golden rule: report only what the data shows

Do **not** invent numbers. Every figure in the report must be traceable to a
specific row in a results CSV (or the BigQuery sink). If a metric is missing for a
cell, write `n/a` and say why (e.g. "scorer not run", "run failed"). Never average
across runs that used different agents or difficulties without labeling it.

## Step 1 — Locate the result runs

EvalBench writes one directory per run: `results/<job_id>/` containing:

| File | What it holds |
| --- | --- |
| `summary.csv` | Per-metric aggregate for the run: `metric_name, metric_score, correct_results_count, total_results_count, job_id, run_time` |
| `scores.csv` | Per-scenario per-scorer rows: `comparator, comparison_error, comparison_logs, ..., id, job_id, score` (the `comparison_logs` field holds the judge's reasoning text) |
| `evals.csv` | Full per-scenario record: `conversation_history`, `accumulated_tools`, `accumulated_skills`, `prompt`, `returncode`, `stdout`, `stderr` |
| `configs.csv` | Run metadata as `job_id, run_time, config, value` key-value rows (see Step 2) |
| `setup_execution.log`, `teardown_execution.log` | The hermetic reset (tier/flags reset, test dbs/users/instances deleted) before and after the run |

By default `results/` is under the EvalBench repo root
(`/usr/local/google/home/prernakakkar/senseai/evalbench/results`). If the user
points at a different directory, use that. Consider only runs created for THIS
experiment — filter by `configs.csv` rows where
`experiment_config.product_name == cloudsql`.

## Step 2 — Classify each run (the key join)

For every `job_id`, read `configs.csv` and extract these `config` rows:

- `experiment_config.eval_group` → **`mcp`** or **`gcloud-only`** (the comparison axis)
- `experiment_config.dataset_config` → derive **difficulty** from the filename
  (`cloud_sql_easy.json` → easy, `_medium` → medium, `_hard` → hard)
- `model_config.generator` → **agent** (`agy_cli`, `claude_code`, `codex`, `gemini_cli`)
- `run_time` → use to pick the LATEST run when there are duplicates for the same
  (agent, difficulty, eval_group) triple, unless the user asks to aggregate all.

This gives each run a label: `(agent, difficulty, eval_group)`. The MCP-vs-gcloud
comparison is: for fixed `(agent, difficulty)`, compare `mcp` vs `gcloud-only`.

## Step 3 — Pull the metrics

From each run's `summary.csv`, read `metric_score` for these `metric_name`s:

- `goal_completion` — **primary** success metric (LLM-judged; the only
  apples-to-apples metric across modes). Higher is better.
- `turn_count`, `end_to_end_latency`, `tool_call_latency`, `token_consumption` —
  efficiency. Lower is better.
- `trajectory_matcher` — **interpret with care.** In `mcp` mode it scores against
  the `cloud-sql__*` golden path. In `gcloud-only` mode tools run via Bash
  (`filter_native_tools: false`), so trajectory is **not** comparable across
  modes — report each mode's value but never diff them as if equivalent.
- `behavioral_metrics`, `parameter_analysis` — qualitative signals
  (hallucination/clarification counts, argument correctness). Summarize, don't
  over-quantify.

For failure detail and guardrail outcomes, read `scores.csv`: the `score` per
`(id, comparator)` and the `comparison_logs` reasoning. Guardrail/false-premise
CUJs (categories `*-guardrail`, `*false-premise`, `constraint-*`,
`input-validation`) are PASS when the agent refuses/clarifies and does NOT mutate —
read the reasoning, not just the number.

## Step 4 — Compose the report

**Write it as a shareable, self-contained document** (something that can be
pasted into a Doc and sent to stakeholders who have no access to this repo):
- Start with a **title block**: title, one-line subtitle, date, owner, and a
  one-line status (Final / Interim N-of-M). Add a short **TL;DR** line up top.
- **No internal artifacts in the body** — do not reference `/tmp/...`, script
  names, or raw `job_id`s in the narrative. Put run IDs and file provenance in an
  **Appendix**.
- **Define acronyms on first use** (CUJ = Critical User Journey; MCP = Model
  Context Protocol). Spell out what "MCP mode" vs "gcloud mode" means once.
- Use clean Markdown tables, bold the winner per cell, keep prose tight, and make
  every number traceable (cite the appendix, not inline paths).
- Professional, neutral tone; phrase caveats as "limitations," not debug notes.

Sections (unless the internal doc specifies otherwise):

1. **Executive summary** — 3-6 bullets: does MCP beat gcloud on goal completion?
   At what efficiency cost (turns/latency/tokens)? Any difficulty where the verdict
   flips? The headline data-plane finding (see below).
2. **Setup / environment** — a table documenting exactly what was run, so the
   experiment is reproducible. Derive these from the config files and `configs.csv`
   (do NOT guess):
   - **evalbench version** (from the run banner / `evalbench.py` `EvalBench vX`).
   - **Per agent: harness/CLI version + agent model.** Read the `model_config`
     path in each run's `configs.csv` (`experiment_config.model_config`), then read
     that YAML for the version + model fields:
       - claude → `claude_code_version` (e.g. `@anthropic-ai/claude-code@latest`)
         and `model` (e.g. `claude-opus-4-8`, served via Vertex on
         `astana-evaluation`).
       - codex → `codex_cli_version` (e.g. `@openai/codex@latest`) and `model`.
       - agy → agy CLI version (`agy --version`) and `model` (the agy UI label,
         e.g. `Gemini 3.5 Flash (High)`).
     Note any agent excluded and why (e.g. **gemini_cli dropped — deprecated**).
   - **Simulated-user model** and **judge/scorer model** (e.g. gemini-3.1-pro
     Vertex) from `simulated_user_model_config` / each scorer's `model_config`.
   - **Scorers enabled** (goal_completion, trajectory_matcher, turn_count,
     end_to_end_latency, tool_call_latency, token_consumption, behavioral_metrics,
     parameter_analysis) and the score scale (0–100).
   - **Project** (`astana-evaluation`), **fixtures** (`nl2code-bbf9a3`,
     `my-pg-app-bbf9a3`), **runners** (medium/hard use `agent_runners: 1`),
     hermetic setup/teardown script, date range of runs, and the `job_id`s feeding
     the report (cite them).
3. **What the CUJs test (dataset description)** — describe the benchmark itself:
   - The two tool configurations compared: **Google-managed MCP**
     (`sqladmin.googleapis.com/mcp`, `google_credentials`) vs **gcloud-only**
     (agent shells `gcloud sql` / `psql` via Bash; no MCP).
   - **~12 CUJs per difficulty tier** (easy/medium/hard) and the rubric: easy =
     single-turn read-only discovery; medium = one mutation + verify / short
     multi-turn / constraint recognition; hard = multi-resource orchestration,
     async op polling, fleet audits, data-plane e2e, destructive-op guardrails.
   - The **four themes**: instance admin; databases & users; backups & operations;
     diagnostics/multi-step — plus special categories (guardrail / false-premise /
     input-validation / data-plane) and what "success" means for guardrails
     (correctly refusing/clarifying, NOT mutating).
   - Hermeticity: every mutating CUJ uses only teardown-managed resource names.
4. **Headline comparison table** — rows = `(agent, difficulty)`, columns =
   `goal_completion (mcp)`, `goal_completion (gcloud)`, `Δ`, then turn_count,
   latency, token_consumption for each mode. Bold the winner per cell.
5. **Per-difficulty breakdown** — easy / medium / hard, with the per-metric
   mcp-vs-gcloud deltas and a one-line takeaway each.
6. **Per-theme breakdown** — group scenario `id`s by category prefix
   (instance-admin, databases-users, backups-operations, diagnostics) and report
   goal_completion by theme × mode.
7. **Capability-gap & data-plane finding** — the sqladmin MCP exposes BOTH
   control-plane and data-plane tools (`execute_sql` / `execute_sql_readonly`),
   but it has **no** `delete_*`, `list_databases`, `list_backups`, or
   `list_operations` tool. Report two things: (a) how each mode handled the
   data-plane CUJs (`csql-hard-ecommerce-e2e`, `csql-hard-data-plane-rowcount`,
   `csql-easy-list-databases`) — MCP can do these via `execute_sql*`, gcloud via
   `psql`/`gcloud sql`; and (b) the MCP capability gaps where MCP mode must give
   up or shell out vs gcloud doing it directly (delete/backup-list CUJs). This
   capability delta is the core strategic signal of the experiment.
8. **Notable failures & guardrails** — pull 3-8 illustrative `comparison_logs`
   snippets (quote them) showing where a mode failed, hallucinated, skipped a
   confirmation, or correctly refused a destructive op.
9. **Limitations** — trajectory not comparable across modes; LLM-judge variance;
   real-GCP latency noise; any failed/partial runs excluded; small N; agent model
   asymmetry (claude=Opus 4.8 vs agy=Gemini 3.5 Flash vs codex) — compare
   MCP-vs-gcloud WITHIN an agent, not raw agent-vs-agent.
10. **Recommendations** — 3-5 concrete, decision-oriented takeaways for a reader
    choosing between the Google-managed MCP and gcloud (e.g. when MCP's
    lower-latency typed tools win, where the capability gaps force gcloud, what to
    fix in the MCP surface). Tie each to evidence in the tables.
11. **Appendix** — run provenance for reproducibility: the per-config run IDs,
    date range, exact config/model files, and any incomplete/timed-out configs.
    This is where internal identifiers live (kept out of the body).

## Optional — BigQuery sink

If the run config has `reporting.bigquery.gcp_project_id` (e.g. `cloud-db-nl2sql`),
the same scores are also in BigQuery. If the user prefers BigQuery and `bq` is
available, you may query it instead of the CSVs, but apply the same
classification (Step 2) and metric selection (Step 3). Otherwise default to the
local CSVs.

## Quality checklist before returning the report

- [ ] Every number cites a `job_id` (and file).
- [ ] MCP and gcloud rows are matched on the SAME `(agent, difficulty)`.
- [ ] `trajectory_matcher` is never diffed across modes.
- [ ] Guardrail/false-premise CUJs judged on refusal behavior, not tool count.
- [ ] The data-plane finding is stated explicitly.
- [ ] If the internal doc link is set, its structure was fetched and followed.
- [ ] Missing data shown as `n/a` with a reason — nothing fabricated.
