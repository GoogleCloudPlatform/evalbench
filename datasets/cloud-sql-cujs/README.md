# Cloud SQL CUJs — Google-managed MCP vs gcloud experiment

EvalBench dataset for comparing how an agent performs Cloud SQL tasks **via the
Google-managed MCP server** (`https://sqladmin.googleapis.com/mcp`,
`authProviderType: google_credentials`) versus **via the `gcloud sql` CLI** run
through the agent's native Bash tool (no MCP). Same CUJs, two tool configs.

Everything here is repo-relative and self-contained.

## Layout

```
cloud_sql_easy.json     12 read-only CUJs (single-turn, single-tool)
cloud_sql_medium.json   12 CUJs (one mutation + verify, guardrails, short multi-turn)
cloud_sql_hard.json     12 CUJs (orchestration, fleet audits, async, data-plane e2e)
model_configs/          per-agent model configs; *_gcloud_*.yaml omit the MCP block
scripts/                teardown_evalset.sh (hermetic reset, set_up + tear_down)
skills/                 evalbench-cloudsql-report SKILL.md (for the agy CLI report)
cloudsql_<agent>_<diff>_config.yaml         MCP mode
cloudsql_<agent>_gcloud_<diff>_config.yaml  gcloud-only mode
```

Agents: `agy_cli`, `claude`, `codex`, `gemini_cli`. Difficulties: `easy`, `medium`,
`hard`. → 4 × 2 × 3 = 24 run configs.

## CUJ themes

Instance admin · Databases & users · Backups & operations · Diagnostics/multi-step.
Includes happy paths plus guardrails (destructive-op confirmation, constraint
recognition, false-premise traps) and the data-plane e-commerce end-to-end CUJ
(`csql-hard-ecommerce-e2e`).

## Comparison metric notes

- `goal_completion` is the **only apples-to-apples** metric across modes.
- `trajectory_matcher` is MCP-golden-path (`cloud-sql__*`) in MCP mode and
  Bash-credited (`filter_native_tools: false`) in gcloud mode — **do not diff it
  across modes**.
- `turn_count`, latencies, `token_consumption` are efficiency signals.

## Hermeticity

`scripts/teardown_evalset.sh` is wired as **both** `set_up_script` and
`tear_down_script`, so it resets state before *and* after each run: resets tier &
clears flags on `my-pg-app-bbf9a3`, deletes test dbs `analytics`/`user_data`, user
`app_ro`, and provisioned instances `eval-app-bbf9a3`, `nl2code-bbf9a3-staging`,
`prod-orders-bbf9a3`, `cheap-ha-bbf9a3`, `commanded`. Every mutating CUJ only uses
names in that allow-list. Pre-seeded persistent fixtures: `nl2code-bbf9a3`
(read-only reference / clone source) and `my-pg-app-bbf9a3` (mutation target).

medium/hard configs set `runners.agent_runners: 1` because Cloud SQL serializes
operations per instance (parallel mutations on the same instance would 409).

## Run

```bash
# one config
EVAL_CONFIG=datasets/cloud-sql-cujs/cloudsql_agy_cli_easy_config.yaml ./evalbench/run.sh
```

## Report

After runs land in `results/<job_id>/`, use
`skills/evalbench-cloudsql-report/SKILL.md` (importable into the agy CLI) to
produce the MCP-vs-gcloud comparison report. Add your internal documentation link
in that file (`> Internal documentation: <ADD LINK>`).
