# Cloud SQL Agent Evaluation: MCP Toolbox **Prebuilt Tools** vs **Skills**

**For Claude (Opus 4.8): does delivering the same Cloud SQL toolbox as a prebuilt MCP server vs. as agent skills change how well / how efficiently the agent works?**

| | |
|---|---|
| **Date** | 2026‑06‑30 |
| **Owner** | Prerna Kakkar |
| **Framework** | EvalBench v1.0.0 |
| **Status** | **Final** — 6 configurations (1 agent × 2 delivery modes × 3 difficulty tiers). Prebuilt = full `cloud-sql-postgres` toolset (admin + data plane). |
| **Internal documentation** | `<ADD LINK>` |

> **TL;DR.** Same underlying toolbox, two delivery mechanisms. With the **full `cloud-sql-postgres` prebuilt (≈50 tools: admin control‑plane + data‑plane incl. `execute_sql`)**, the **Prebuilt MCP edges Skills on success (36/36 vs 33/36) and is faster and cheaper at every tier** — one typed tool call per action vs. the skills' "read SKILL.md → run a node script" two‑step. Use Prebuilt MCP as the default; Skills remain useful for their curated, guided workflows.

---

## 1. Executive summary

- **Prebuilt (full toolset) leads on success:** Prebuilt **36/36** vs Skills **33/36** — adding the data‑plane tools lifted Prebuilt from 32→36 (it now passes the list‑databases and create‑database CUJs via typed `execute_sql`/`list_databases` instead of struggling).
- **Prebuilt MCP is faster and lower‑token at every tier:** easy 10.2 s vs 12.2 s, medium 51.6 s vs 67.3 s, hard 238 s vs 308 s; fewer tokens throughout. A typed MCP call replaces the skills' invoke‑skill‑then‑shell‑to‑node‑script round trip.
- **Prebuilt now covers the data plane:** the full `cloud-sql-postgres` prebuilt includes `execute_sql`, `list_tables/schemas/views/indexes`, `generate_query`, query plans/metrics, etc. — used 8–10× in the medium/hard runs.
- **Remaining data‑plane caveat (both modes):** the toolbox data connection is pinned to one instance (`my-pg-app-bbf9a3`), so a CUJ that provisions a *new* instance and queries it (the e‑commerce e2e) still drops to `psql`/Bash — this affects Prebuilt and Skills equally.

---

## 2. Bottom line — which to use, and when

| Situation | Recommended | Why |
|---|---|---|
| Control‑plane automation (create/list/clone instances, DBs, users, backups) | **Prebuilt MCP** | One typed call per action; lowest latency & tokens; 12/12 every tier |
| Data‑plane on a connected instance (SQL, schema, query plans) | **Prebuilt MCP** | Full toolset includes `execute_sql` + rich data/observability tools |
| Latency / cost sensitive, high volume | **Prebuilt MCP** | Faster & fewer tokens at every tier |
| Curated, guided multi‑step workflows / human‑readable scaffolding | **Skills** | SKILL.md guidance; useful when you want explicit, inspectable steps |
| Data‑plane on a *freshly provisioned* instance | either (falls back to `psql`) | Toolbox connection is pinned to one instance in both modes |

**Net:** standardize on the **full `cloud-sql-postgres` Prebuilt MCP** for Cloud SQL automation with Claude — it now matches Skills' coverage, beats them on success, and is faster/cheaper. Keep Skills where you specifically want the curated skill scaffolding.

---

## 3. What was tested

Same 36 CUJs as the MCP‑vs‑gcloud study (12 each easy/medium/hard; themes: instance admin, databases & users, backups & operations, diagnostics), run for Claude under two deliveries of the **same** Cloud SQL toolbox:

- **Prebuilt MCP** — `googleapis/mcp-toolbox` served as an MCP server: `npx -y @toolbox-sdk/server --prebuilt cloud-sql-postgres --stdio`. The **full `cloud-sql-postgres` toolset (~50 tools)**: control‑plane (`create_instance`, `get_instance`, `clone_instance`, `create_database`, `create_user`, `list_instances`, `list_databases`, `create_backup`, `restore_backup`, `wait_for_operation`, …) **plus data‑plane** (`execute_sql`, `list_tables/schemas/views/indexes`, `generate_query`, `get_query_plan/metrics`, `list_active_queries`, …) and observability. (This superset is why loading `cloud-sql-postgres-admin` *and* `cloud-sql-postgres` together collides — the full set already contains admin.)
- **Skills** — `gemini-cli-extensions/cloud-sql-postgresql` installed as agent skills: **Admin, Data, Health, Lifecycle, Monitor, Replication, View‑Config**. Each is a `SKILL.md` + node scripts that wrap the same toolbox.

---

## 4. Method & environment

| Component | Detail |
|---|---|
| Agent | **Claude Code, Opus 4.8** (`@anthropic-ai/claude-code@latest`) |
| Prebuilt server | `@toolbox-sdk/server --prebuilt cloud-sql-postgres --stdio` (MCP over stdio; full admin+data toolset) |
| Skills | `gemini-cli-extensions/cloud-sql-postgresql` via `install_from_repo` |
| Connection | project `astana-evaluation`, instance `my-pg-app-bbf9a3`, db `postgres` (user/password) |
| Simulated user & judge | Gemini 3.1 Pro (Vertex) |
| Metrics | goal_completion (LLM‑judged, 0–100, ≥50 = pass /12), turns, latency, tokens, behavioral/parameter |
| Isolation | Hermetic allowlist teardown before & after each config; **fake‑home reset before every config** so Prebuilt = MCP‑only and Skills = freshly‑installed skills (no cross‑contamination) |
| Execution | Sequential; `agent_runners: 1` on medium/hard |

---

## 5. Results — task success (passes out of 12; **bold** = mode winner)

| Tier | Prebuilt MCP | Skills |
|---|---|---|
| Easy | **12** | **12** |
| Medium | **12** | 11 |
| Hard | **12** | 10 |
| **Total** | **36 / 36** | **33 / 36** |

Prebuilt (full toolset) matches or beats Skills at every tier; the gain over the earlier admin‑only prebuilt (32/36) came entirely from the added data‑plane tools.

---

## 6. Results — efficiency (average per task)

| Tier | Mode | Turns | Latency | Tokens |
|---|---|---|---|---|
| Easy | Prebuilt | 1.00 | **10.2 s** | **2,795** |
| Easy | Skills | 1.00 | 12.2 s | 3,341 |
| Medium | Prebuilt | 1.25 | **51.6 s** | **3,649** |
| Medium | Skills | 1.41 | 67.3 s | 6,955 |
| Hard | Prebuilt | 1.33 | **238.0 s** | **6,168** |
| Hard | Skills | 1.33 | 308.0 s | 7,079 |

**Prebuilt MCP is faster and uses fewer tokens at every tier** — the most consistent difference. The skills path spends extra turns/tokens reading the SKILL.md and shelling out to node scripts.

---

## 7. Case studies — how the flow differs (from real traces)

### A — List databases (`csql-easy-list-databases`)
- **Prebuilt MCP:** a single typed call (`cloud-sql-postgres__list_databases`) → answer.
- **Skills:** two steps — `Skill` (reads `cloud-sql-postgres-admin/SKILL.md`) then `Bash` running `node .../scripts/list_databases.js`.
- **Why it matters:** identical result; the prebuilt MCP is one structured call vs. the skills' read‑instructions + spawn‑node‑script round trip — the mechanism behind the latency/token gap.

### B — Data‑plane via `execute_sql` (medium create‑database & data CUJs)
- **Prebuilt MCP (full toolset):** now uses the typed **`cloud-sql-postgres__execute_sql`** directly for create‑database and data‑plane reads on the connected instance (used 8–10× across medium/hard) — no shell needed. This is the change that lifted prebuilt to 12/12 on easy & medium.
- **Skills:** routes the same work through the Data skill's node scripts (`Skill` + `Bash`).
- **E‑commerce e2e caveat:** that CUJ provisions a *new* instance (`prod-orders-bbf9a3`); because the toolbox data connection is pinned to `my-pg-app-bbf9a3`, **both** modes fall back to `psql`/Bash to run SQL against the new instance — a connection‑scoping limitation, not a tool‑coverage one.

### C — Naming/guardrail nuance
On the e‑commerce task the agent paused to reason about the instance **name** before proceeding (e.g. flagging the `prod-orders*` naming), an example of the extra deliberation these workflows can induce (more turns, sometimes better judgment).

---

## 8. Limitations

- Single agent (Claude Opus 4.8) — no cross‑agent generalization.
- Toolbox **data connection is pinned to one instance** (`my-pg-app-bbf9a3`) for both modes, so data‑plane work against a *newly provisioned* instance falls back to `psql`/Bash.
- LLM‑judge variance; small N (12/cell); real‑cloud latency noise.
- Token counts are harness‑internal; hard‑tier latency includes provisioning time.

---

## 9. Recommendations

1. **Use the full `cloud-sql-postgres` Prebuilt MCP as the default for Cloud SQL automation with Claude** — best success (36/36), lowest latency and tokens, and it now covers the data plane.
2. **Prefer Skills when you want curated, inspectable, guided workflows** (or the Health/Monitor/Replication framing) — but expect higher latency/token cost and slightly lower success here.
3. **Load the full prebuilt, not the admin‑only subset** — the admin‑only toolset (32/36) lacks `execute_sql`; the full toolset closes that gap.
4. **For data‑plane work on freshly provisioned instances, expect a `psql` fallback** in both modes unless the toolbox is reconnected to the new instance.

---

## Appendix — run provenance

- **Run IDs (final).** Prebuilt (full `cloud-sql-postgres`): easy `9eb41dca`, medium `e693c2db`, hard `5353311e`. Skills: easy `8567a43f`, medium `f5654ab6`, hard `6fdbaa6b`. (project `astana-evaluation`; BigQuery sink `cloud-db-nl2sql`.)
- **Hermeticity & mode‑purity (audited).** Allowlist teardown + fake‑home reset (wipe `plugins/`+`skills/`, bare `settings.json`) before every config. Audit of all runs: **0 `plugin_dak`** anywhere; **Prebuilt** used ONLY `cloud-sql-postgres__*` MCP tools (incl. `execute_sql`; 0 `Skill`/node‑script calls); **Skills** used ONLY `Skill` + `cloud-sql-postgresql/.../*.js` (0 MCP tool calls). No cross‑leakage.
- **Viewer:** runs uploaded to the shared mesop viewer as `cloudsql-toolbox-<prebuilt|skills>-<difficulty>`.
- Run window: 2026‑06‑29 → 2026‑06‑30.
