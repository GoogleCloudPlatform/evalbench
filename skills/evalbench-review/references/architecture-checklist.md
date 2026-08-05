# EvalBench architecture checklist

Contracts a change must satisfy. Each item names the file that defines the
contract — cite it when reporting a violation.

Background: `AGENTS.md` (architecture overview, lifecycle diagrams, config
schemas) and `docs/architecture.md` (external dependency graph).

## Layering

The pipeline is `Orchestrator → Evaluator → Work items → Generator/Scorer → Reporting`.
Logic belongs to exactly one layer:

| Layer | Directory | Owns |
|---|---|---|
| Orchestrator | `evaluator/*orchestrator.py` | Dataset breakdown, per-dialect/DB fan-out, parallel stages |
| Evaluator | `evaluator/*evaluator.py` | One scenario's lifecycle, turn loop, termination |
| Work | `work/` | A single unit dispatched to the runner pool |
| Generator | `generators/models/` | Driving the *tested* system (CLI or SDK) |
| Scorer | `scorers/` | Turning a completed eval into a number |
| Reporting | `reporting/` | CSV, BigQuery, GCS artifacts |

Findings to look for: scoring logic inside an evaluator; a generator deciding
whether a scenario is finished; an orchestrator reaching into a generator's
internals; a work item that talks to reporting directly.

## Scorers

**Base class:** `scorers/comparator.py` — `Comparator`.

- Subclass `Comparator` and set `self.name` in `__init__` to the config key
  string (e.g. `self.name = "trajectory_matcher"` in
  `scorers/trajectorymatcher.py:35`). The name is what surfaces in CSV and
  BigQuery output.
- Implement `compare(...)` with the **full positional signature** from the base
  class: `nl_prompt, golden_query, query_type, golden_execution_result,
  golden_eval_result, golden_error, generated_query, generated_execution_result,
  generated_eval_result, generated_error, database="", **kwargs`. Callers pass
  positionally — reordering, renaming or dropping a parameter silently shifts
  every argument. Agentic scorers reuse the golden/generated slots for
  trajectories rather than adding parameters.
- Return `Tuple[float, str]` — score and human-readable analysis. The analysis
  string is what a user reads when debugging a low score; "0" with an empty
  explanation is a finding.
- `__init__` takes the scorer's config dict. Read options with `.get()` and a
  documented default so an empty `{}` config works.
- **Register the config key** with a branch in `scorers/score.py`. Multi-trial
  comparators register separately in `scorers/multi_trial_score.py`. An
  unregistered key is silently ignored at runtime.
- **Document it** in `docs/scorers.md`: a row in the right category table, and
  an options table if it takes config. `docs/configs/run-config.md` lists the
  NL2SQL-relevant subset.
- Workflow-specific scoring should use the existing `python_scorer` rather than
  adding a scorer to the framework — see `docs/scorers.md#custom-scorers`. A new
  first-class scorer needs a general-purpose justification.

## Generators

**Base class:** `generators/models/generator.py` — `QueryGenerator`.

- Implement **`generate_internal`**, never override `generate`. The base
  `generate` wraps the call in `util/rate_limit.rate_limit` with the semaphore,
  `execs_per_minute` and `max_attempts` from config. Overriding `generate`
  bypasses rate limiting and retry, and shows up in production as
  `ResourceExhaustedError` storms.
- Call `super().__init__(config)` so the semaphore and attempt budget are set up.
- **Register in the `generators` dict** in `generators/models/__init__.py`
  (`get_generator`). The dict key is the `generator:` value in the model config
  YAML. Unknown keys raise `ValueError: Unknown Generator`.
- Instances are cached per `model_config_path` under a lock in `get_generator`
  and shared across worker threads — a generator holding per-scenario mutable
  state is a concurrency bug (see Concurrency below).
- Agent CLI generators (`gemini_cli`, `claude_code`, `codex_cli`, `agy_cli`)
  additionally must:
  - Run inside the sandboxed home (`.venv/fake_home*`) — never the real `$HOME`.
  - Install MCP servers / extensions / skills **idempotently**; a re-run must
    not duplicate or fail.
  - Emit tool names in canonical `<server>__<tool>` form via
    `generators/models/tool_naming.py`. Trajectory scorers are
    generator-agnostic and expect canonical names; per-harness normalization
    belongs in the adapter, not the scorer.
  - Have a per-harness doc: `docs/{gemini,claude_code,codex,agy}_cli_agent_testing.md`.

## Work items and concurrency

**Base class:** `work/work.py` — `Work`, with `run(work_config)`.
**Runner:** `mp/mprunner.py` — a `ThreadPoolExecutor`, sized by `runners:` in
the run config (default 10 agent runners).

Despite the `mp` name these are **threads sharing one address space**, which
makes shared mutable state the dominant risk:

- No mutable module-level state, no writing to shared dicts without a lock. The
  reference pattern is `get_generator` in `generators/models/__init__.py`:
  acquire `global_models["lock"]` before touching `registered_models`.
- Generator instances are cached and shared across worker threads. Anything
  derived per scenario (temp dirs, sandbox paths, session IDs, conversation
  history) must be a local or keyed by scenario — never an attribute mutated on
  the shared instance.
- Per-run ambient state propagates via `contextvars` (`util/context.py`), not
  globals. A value stashed in a module global leaks between concurrent scenarios.
- `os.chdir`, `os.environ` mutation and other process-global calls are unsafe
  here: they affect every in-flight scenario. Pass `cwd`/`env` to the subprocess
  instead.
- Rate-limited external calls go through `util/rate_limit.py`, not a bare
  `time.sleep` retry loop.
- Values written to the Redis result cache are read back through
  `util/safe_pickle.py`, whose allowlist rejects unknown globals. Caching a new
  custom type will fail deserialization unless it's a permitted value type —
  store a primitive representation instead of widening the allowlist.
- Reviewing a concurrency change: ask whether it still behaves with
  `agent_runners: 1` **and** with the default 10.

## Imports and packaging

- Imports are relative to the `evalbench/` directory on `PYTHONPATH`:
  `from scorers import trajectorymatcher`, `from util.config import load_yaml_config`.
  **Not** `from evalbench.scorers import ...` — that breaks CI, `run.sh` and the
  PyInstaller binary.
- New top-level modules under `evalbench/` are picked up by
  `[tool.setuptools.packages.find] include = ["evalbench*"]`. A new package
  directory needs an `__init__.py`.
- A dependency loaded dynamically, or one shipping data files or requiring
  package metadata, needs an entry in `pyinstaller.spec`
  (`packages_to_collect` / `packages_needing_metadata`) or the `make binary`
  build silently produces a binary that fails at runtime.

## Configuration

- **Nothing environment-specific is hardcoded.** GCP projects, regions, model
  IDs, CLI versions, dataset paths, MCP URLs all come from the run config, the
  model config, or a scenario's `env` block.
- Config is loaded with `util/config.load_yaml_config` (`pyaml_env`), which
  expands `!ENV` references — don't add a bespoke YAML loader.
- New config keys need: a `.get()` default that keeps existing configs working,
  documentation in the matching `docs/configs/*.md`, and a note in the run
  config example if it's commonly used.
- Config keys live at the level that owns them. Recent precedent: `product_name`
  moved from scorer-specific settings up to top-level experiment config (#539)
  because more than one scorer needed it. A key duplicated across two scorers
  belongs one level up.
- Removing or renaming a key is a breaking change for every checked-in dataset
  in `datasets/` — grep before accepting it.

## Datasets

- Agentic scenarios carry `id`, `starting_prompt`, `conversation_plan`,
  `expected_trajectory`, optional `env`, and `max_turns` (see `AGENTS.md`
  and `docs/configs/agentic-dataset-config.md`).
- `expected_trajectory` entries use canonical `<server>__<tool>` names.
- Parsing lives in `dataset/` (`evalinput.py`, `evalgeminicliinput.py`, …); a
  new dataset format needs a parser there plus a `dataset_format` value, not
  special-casing inside an evaluator.
- Datasets must not embed real credentials, internal project IDs, or customer
  data.

## Reporting

- A scorer's output reaches CSV and BigQuery through the standard score path in
  `reporting/report.py` / `csv.py` / `bqstore.py`. Don't add bespoke columns.
- BigQuery writes are append-only against an existing schema; a new field is a
  schema migration, not a code detail. Flag it.
- `reporting/gcs_artifact.py` handles artifact upload — check for leaked
  absolute local paths in what gets stored.

## Dependencies

- Every pin in `pyproject.toml` gets a comment saying *why* it's pinned and what
  lifts it. The existing `mcp>=1.8,<2` and `pyOpenSSL<26.2` entries are the
  template. An unexplained pin is a finding.
- A new dependency needs an edge in the `docs/architecture.md` graph and a real
  import under `evalbench/` — the graph is documented as "no unused deps".
- New deps also affect `uv.lock`, the container build (`Makefile`,
  `cloudbuild.yaml`) and the PyInstaller binary. A lockfile that wasn't
  regenerated is a finding.

## Isolation

The stated core design principle (`AGENTS.md`): every multi-turn execution runs
in a sandboxed home directory to keep the developer's machine clean.

- No writes outside the run's sandbox and `results/`.
- No mutation of the user's real `~/.gemini`, `~/.claude`, `~/.codex` or
  `~/.config`.
- No global CLI installs as a side effect of a run.
- Sandbox state must be recreatable after `rm -rf .venv/fake_home*`.

## Docs

`docs/` is the canonical user-facing reference; `AGENTS.md` is the
agent/onboarding overview. Both go stale silently.

Check that the change updated:

- `docs/scorers.md` — new or changed scorer, or changed config options
- `docs/configs/*.md` — new or changed config keys
- `docs/{harness}_agent_testing.md` — generator behavior changes
- `docs/architecture.md` — dependency changes
- `AGENTS.md` — new component in the module layout or a lifecycle change
- `README.md` — only for user-visible entry-point changes

Docs that now contradict the code are a `docs` finding, not a nit.
