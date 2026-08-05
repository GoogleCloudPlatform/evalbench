# Verification playbook

How to actually run EvalBench's checks, and what a healthy result looks like.

## Environment prerequisites

Three things bite before any test runs.

### 1. Protobuf stubs are gitignored and must be generated

Without them every test module fails at collection with
`ImportError: cannot import name 'eval_request_pb2' from 'evalproto'`.

```bash
make proto
```

Equivalent to what CI runs (`.github/workflows/pytest.yml`):

```bash
python -m grpc_tools.protoc \
  --proto_path=evalbench/evalproto \
  --python_out=evalbench/evalproto \
  --pyi_out=evalbench/evalproto \
  --grpc_python_out=evalbench/evalproto \
  --experimental_editions evalbench/evalproto/*.proto
```

### 2. PYTHONPATH must include `evalbench/`

Imports across the codebase are relative to the `evalbench/` package directory
(`from scorers import ...`, not `from evalbench.scorers import ...`). CI sets:

```
PYTHONPATH: $GITHUB_WORKSPACE/evalbench:$GITHUB_WORKSPACE
```

Locally, running pytest from inside `evalbench/` achieves the same thing.

### 3. Eager imports mean one missing dep fails the whole collection

`generators/models/__init__.py` imports every generator at module load, so a
single missing dependency breaks collection for unrelated tests — and it
surfaces one package at a time. On a machine behind a corporate pip proxy:

```bash
python -m pip install <pkg> --index-url https://pypi.org/simple
```

Packages historically missing from a stale local `venv/`: `pytest`,
`google-cloud-firestore`, `google-cloud-geminidataanalytics`, `a2a-sdk`,
`google-cloud-dataform`.

The supported path is `uv`:

```bash
uv venv && source .venv/bin/activate && uv sync
```

## The checks

### Unit tests

```bash
cd evalbench && python -m pytest -q --ignore=evalbenchtest
```

Targeted, while iterating:

```bash
cd evalbench && python -m pytest test/trajectory_matcher_test.py -vv
```

CI equivalent: `uv run --with pytest pytest evalbench/test -vv`.
`make test` runs the same suite through `nox`.

### Style — a required CI check

```bash
pycodestyle evalbench --config=.pycodestyle
```

`.pycodestyle` ignores `E402, E501, W503, W504`, caps lines at 160, and excludes
`evalbench/evalproto`. `make style` additionally excludes `evalbench/lib*`.
Note CI runs this on Python 3.8 while the package requires >=3.10 — syntax
newer than 3.8 can pass locally and still trip the linter.

### Known-good baseline

As of 2026-08-01: **433 passed, 1 failed.**

The one expected failure is
`test/bigtable_test.py::TestBigtable::test_get_metadata`, which needs the
Bigtable Admin API enabled on the GCP project and fails with `PermissionDenied`.
It is unrelated to any change.

Re-derive the baseline on `main` if the numbers have drifted, rather than
assuming this one still holds. Report deltas against the baseline, never raw
red/green.

### Tests that need credentials

`alloydb_test.py`, `spanner_test.py`, `bigtable_test.py`, `mongodb_test.py` and
the GCP-backed scorer tests may skip or error without
`gcloud auth application-default login` and:

```bash
export EVAL_GCP_PROJECT_ID=your_project_id
export EVAL_GCP_PROJECT_REGION=us-central1
```

A skip is not a pass. If a change touches a dialect whose tests skipped, say so
and mark it unverified.

## Verifying things pytest cannot see

### Run-config / model-config / dataset changes

No Python test loads these. Validate manually:

```bash
python -c "from pyaml_env import parse_config; import json; \
  print(json.dumps(parse_config('datasets/bat/example_run_config.yaml'), indent=2))"
```

Then check required keys against the docs:

- `docs/configs/run-config.md` — orchestrator, scorers, runners, reporting
- `docs/configs/model-config.md` — generator, env, setup (mcp_servers, extensions, skills)
- `docs/configs/dataset-config.md` and `docs/configs/agentic-dataset-config.md`
- `docs/scorers.md` — every scorer's config options

A scorer key that isn't matched in `scorers/score.py` is silently ignored at
runtime — no error, just a missing metric. Grep for the key to confirm it lands.

### A real single-scenario run

Only when tests genuinely can't cover the path, and only after asking the user
(runs cost money and can take a long time):

```bash
export EVAL_CONFIG=datasets/<your>/run_config.yaml
./evalbench/run.sh
```

Force sequential execution first so failures are legible:

```yaml
runners:
  agent_runners: 1
```

If a previous run's sandbox state is suspect:

```bash
rm -rf .venv/fake_home .venv/fake_home_claude .venv/fake_home_agy
```

### Benign noise

`unknown format "google-duration" ignored` in stdout is a harmless JSON-schema
draft warning. Don't report it as a finding.

## Judging test quality, not just test presence

A green suite proves less than it looks. For each new or modified test, check:

- **It would fail without the change.** If the assertion holds on both sides of
  the diff, it isn't covering the change.
- **It asserts on values, not just absence of exceptions.** `self.assertEqual(score, 100.0)`
  beats "it ran".
- **It stubs the model, CLI or database.** A test that reaches a live Vertex
  endpoint is non-deterministic, slow, and will fail in CI. Existing patterns:
  `util/fake_mcp_server.py`, the autouse install stub opted out of by the
  `real_agy_install` marker in `pyproject.toml`.
- **It covers the failure path.** Scorers get malformed input in production —
  empty trajectories, `None` results, errors from the generated query.
- **It matches house style.** Tests are `unittest.TestCase` classes in
  `evalbench/test/<subject>_test.py`, with the `sys.path.append` preamble that
  makes the package importable when run directly.
