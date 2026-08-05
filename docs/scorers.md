# Scorers

Scorers turn a completed evaluation into numbers. Every scorer produces a score (typically 0–100, though some report raw counts or milliseconds) plus optional textual logs explaining the result.

All scorers are enabled from the `scorers:` block of your [run config](/docs/configs/run-config.md). The YAML key selects the scorer; the value is that scorer's configuration. Scorers that need no options take `null` or `{}`:

```yaml
scorers:
  exact_match: null
  trajectory_matcher:
    enforce_order: true
  goal_completion:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml
```

Scorers are additive — enable as many as you need, and each reports as its own row in CSV and BigQuery output.

**Contents**

- [SQL scorers](#sql-scorers)
- [Multi-trial consistency scorers](#multi-trial-consistency-scorers)
- [Agentic scorers](#agentic-scorers)
- [Skills scorers](#skills-scorers)
- [Transform tooling scorers](#transform-tooling-scorers)
- [MCP readability scorers](#mcp-readability-scorers)
- [Custom scorers](#custom-scorers)
- [Adding a scorer](#adding-a-scorer)

---

## SQL scorers

Used for NL2SQL evaluations (DQL, DML, DDL). See the [NL2SQL dataset format](/docs/configs/dataset-config.md) for how golden queries and eval queries are defined.

| Scorer Key | Type | What it Measures |
|---|---|---|
| `exact_match` | Deterministic | Whether the generated query's execution result exactly matches the golden query's result. |
| `recall_match` | Deterministic | Precision and recall between generated and expected results, ignoring `None` and duplicate values. Defaults to recall-based scoring, order-insensitive. |
| `set_match` | Deterministic | Execution accuracy comparing golden and generated query results, as defined by the BIRD methodology. |
| `executable_sql` | Deterministic | Whether the generated query runs at all — 100 if it executes without error, 0 if an error is present. Measures syntactic validity independent of correctness. |
| `returned_sql` | Deterministic | Whether the generated output contains actual SQL rather than only comments or prose. |
| `regexp_matcher` | Deterministic | Whether the generated query matches supplied regex patterns. |
| `llmrater` | LLM | Uses an LLM to compare golden and generated execution results, scoring cases like mismatched column names or extra columns. Requires its own `model_config`. |

### `regexp_matcher` options

| Option | Default | Description |
|---|---|---|
| `regexp_string_list` | *required* | List of regex patterns to match against the generated query. |
| `invert_results` | `false` | When true, non-matching queries score 100 and matching queries score 0. |
| `match_all_patterns` | `false` | When true, score 100 only if all patterns match; otherwise one match suffices. |
| `match_whole_query` | `false` | When true, patterns must match the entire query rather than a substring. |

### `llmrater` options

| Option | Default | Description |
|---|---|---|
| `model_config` | *required* | Path to the [model config](/docs/configs/model-config.md) for the rating LLM. |
| `hybrid_ground_truth` | `false` | When true, if the golden query fails on the target BigQuery engine, fall back to resolving reference rows from the local SQLite database file. |

---

## Multi-trial consistency scorers

These require `num_trials` greater than 1 in the run config. They compare trials of the same prompt against each other rather than against a golden answer, and aggregate at the prompt level using a strict all-or-nothing rule — the prompt is consistent only if **all** trial pairs are consistent.

| Scorer Key | Type | What it Measures |
|---|---|---|
| `exact_match_consistency` | Deterministic | Consistency across trials using exact match on execution results. |
| `llm_consistency` | LLM | Consistency across trials using an LLM to compare results and errors. Requires `model_config`. |

---

## Agentic scorers

Used for multi-turn agent evaluations. See [Agentic evaluations](/docs/agentic-evals.md) for the execution model and the [agentic dataset format](/docs/configs/agentic-dataset-config.md) for how scenarios declare their expectations.

### Deterministic

| Scorer Key | Score Range | What it Measures |
|---|---|---|
| `trajectory_matcher` | 0–100 | Expected vs. actual tool calls. Jaccard similarity by default (order-insensitive); Levenshtein distance with `enforce_order: true`. |
| `turn_count` | Count | Number of user↔agent conversation turns. Lower is generally better. |
| `agent_steps` | Count | Total tool-call round trips the agent made — the internal effort collapsed inside each reply, as opposed to the conversation rounds `turn_count` measures. |
| `end_to_end_latency` | Milliseconds | Total wall-clock latency: model API latency plus tool execution latency. |
| `tool_call_latency` | Milliseconds | Sum of all tool execution durations across all turns. |
| `token_consumption` | Count | Fresh tokens consumed (input + output) across all turns. |
| `tokens_processed` | Count | Every token the model evaluated, including fully cached context layers, unweighted. An absolute index of physical compute performed. |
| `effective_billed_tokens` | Weighted count | Tokens normalized by price weighting, condensing multi-tier pricing into one index correlated with real spend. |

#### `trajectory_matcher` options

| Option | Default | Description |
|---|---|---|
| `enforce_order` | `false` | When true, use Levenshtein distance for order-sensitive matching instead of Jaccard similarity. |
| `filter_native_tools` | `true` | When true, drop native/harness-internal tools (anything not in canonical `<server>__<tool>` form) from both expected and actual lists before scoring. Set to `false` to score native tool usage too. See [tool name format](/docs/configs/agentic-dataset-config.md#tool-name-format). |

#### `effective_billed_tokens` options

Default weights mirror Anthropic Opus price ratios, relative to fresh input at 1.0.

| Option | Default | Description |
|---|---|---|
| `input_weight` | `1.0` | Weight for fresh input tokens. |
| `cached_weight` | `0.1` | Weight for cache reads — cheap replay of cached context. |
| `cache_write_weight` | `1.25` | Weight for cache writes — a premium to establish a cache entry. |
| `output_weight` | `5.0` | Weight for generated output tokens. |

### LLM-based

All of these require a `model_config` pointing at the LLM that performs the evaluation.

| Scorer Key | Score Range | What it Measures |
|---|---|---|
| `goal_completion` | 0–100 | Whether the agent accomplished the `conversation_plan`'s intent. Returns 100 for PASS, 0 for FAIL. |
| `behavioral_metrics` | 0–100 | Hallucination rate and unnecessary-clarification rate in a single pass. Starts at 100 and penalizes 50 per hallucination and 20 per unnecessary clarification. |
| `parameter_analysis` | 100 (qualitative) | Qualitative feedback on the arguments passed to each tool. Always scores 100 — the value is in the textual explanation. |
| `binary_rubric_scorer` | 0–100 | Pass/fail against your own rubric criteria. |

`goal_completion`, `behavioral_metrics`, and `binary_rubric_scorer` accept `include_tool_calls` (default `false`), which adds the full tool-call record to the LLM's context instead of conversation text alone.

`binary_rubric_scorer` reads its criteria from the scenario's `binary_rubric` array and emits one score per criterion, named `binary_rubric_scorer_<index>`. If a scenario declares no rubric, a single unindexed scorer runs instead.

---

## Skills scorers

For evaluating agent skill packages rather than tool calls.

| Scorer Key | Type | What it Measures |
|---|---|---|
| `skills_trajectory` | Deterministic | Expected vs. actually activated skill names. Jaccard set similarity by default. |
| `skills_best_practices` | LLM | Quality of each activated skill's `SKILL.md` — name compliance, description quality, body completeness, absence of TODOs, and progressive-disclosure design. Scores the mean across all evaluated skills. |

### `skills_trajectory` options

| Option | Default | Description |
|---|---|---|
| `enforce_order` | `false` | Use strict Levenshtein sequence alignment instead of Jaccard similarity. |
| `allow_extra_skills` | `false` | Flexible coverage matching — extra activated skills don't reduce the score. Cannot be combined with `enforce_order`. |

### `skills_best_practices` options

| Option | Default | Description |
|---|---|---|
| `model_config` | *required* | Path to the model config for the judging LLM. |
| `skills_dir` | sandbox paths | Directory to resolve `<skill_name>/SKILL.md` from. Falls back to sandbox paths when unset. |

---

## Transform tooling scorers

For evaluating generated Dataform and dbt projects. Compile scorers check that the project builds; run scorers execute it.

| Scorer Key | What it Measures |
|---|---|
| `dataform_compile` | Whether the generated Dataform project compiles locally. |
| `dataform_run` | Whether the generated Dataform project executes locally. |
| `dataform_cloud_compile` | Compilation via the Google Cloud Dataform API. Requires `gcp_project_id` and `gcp_region`. |
| `dataform_cloud_run` | Execution via the Google Cloud Dataform API. Requires `gcp_project_id` and `gcp_region`. |
| `dbt_compile` | Whether the generated dbt project compiles (`dbt compile`). |
| `dbt_run` | Whether the generated dbt project executes (`dbt run`). |

Local Dataform and dbt scorers locate the project by searching for `workflow_settings.yaml` and `dbt_project.yml` respectively.

---

## MCP readability scorers

These apply only when `orchestrator: mcp_readability` is set. Rather than scoring an agent run, they evaluate an MCP endpoint's own tool listing for agent-consumability.

| Scorer Key | Type | What it Measures |
|---|---|---|
| `mcp_tool_metrics` | Deterministic | `total_tools`, `estimated_tokens` (approximated as JSON length ÷ 4, summed across tools), and `token_budget_used_percent` against a configured `token_budget`. Its binary summary metric is "within token budget". |
| `mcp_style_readability` | LLM | Reviews the tool manifest against a style guide from an LLM-agent-consumption perspective, returning P0/P1/P2 findings and an overall readability score. |

---

## Custom scorers

### `python_scorer`

Runs an arbitrary Python script as a scorer, so you can add evaluation logic without forking EvalBench.

| Option | Default | Description |
|---|---|---|
| `script_path` | *required* | Path to the Python evaluation script. |
| `scorer_name` | script basename | Name this scorer instance reports under. |

```yaml
scorers:
  python_scorer:
    script_path: "path/to/your_script.py"
    scorer_name: "my_custom_check"
```

**Contract:**

1. EvalBench runs `uv run --isolated <script_path>` as a subprocess.
2. The complete evaluation context is passed as a JSON object on **stdin**.
3. The script writes a JSON object to **stdout** containing `score` (float) and `reason` (string).

Scripts can declare dependencies with [PEP 723](https://peps.python.org/pep-0723/) inline metadata — `uv run` installs them into an isolated environment automatically:

```python
# /// script
# dependencies = ["requests"]
# ///

import sys
import json

def main():
    input_data = json.load(sys.stdin)
    # ... custom logic ...
    print(json.dumps({"score": 100.0, "reason": "PASS"}))

if __name__ == "__main__":
    main()
```

**Included judge — `hybrid_xa_judge.py`:** setting `script_path: evalbench/scorers/judges/hybrid_xa_judge.py` runs a cross-database Execution Accuracy judge. It compares BigQuery execution results against SQLite references using strict cell normalization: rounding floats to 4 decimal places, sorting rows lexicographically, stripping trailing `.0` string suffixes, and ignoring column headers.

---

## Adding a scorer

Scorers implement the `Comparator` base class in [`evalbench/scorers/comparator.py`](/evalbench/scorers/comparator.py) and are registered by config key in [`evalbench/scorers/score.py`](/evalbench/scorers/score.py). Multi-trial comparators register separately in [`evalbench/scorers/multi_trial_score.py`](/evalbench/scorers/multi_trial_score.py).

If your scorer is specific to your workflow rather than generally useful, prefer [`python_scorer`](#python_scorer) — it needs no changes to EvalBench itself. See [contributing](/docs/contributing.md) for submitting a scorer upstream.
