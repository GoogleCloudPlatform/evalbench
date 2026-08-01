---
name: evalbench-review
description: Review a change in the EvalBench repo for (a) does it actually work — verified by running the tests and style checks, (b) does it follow EvalBench architecture — base-class contracts, config-key registration, PYTHONPATH-relative imports, sandbox isolation, concurrency safety, docs, (c) does it still build and deploy — Docker image, GKE manifests, Cloud Run, Cloud Build, and (d) is it a good PR — under ~400 lines, single-purpose, with a what-and-why description, and clear, simple, concise, maintainable, idiomatic code. Use for reviewing the working diff, a branch against main, or a PR touching scorers, generators, evaluators, orchestrators, work items, datasets, configs, reporting, the Dockerfile or k8s manifests.
when_to_use: Triggers on "review my changes", "review this PR", "did I break anything", "will this break docker/GKE/the deployment", "is this PR too big", "does this follow best practices", "is this the right way to add a scorer/generator/evaluator", "does this follow evalbench architecture", or before opening a PR against main.
allowed-tools: Read, Grep, Glob, Bash(git diff *), Bash(git log *), Bash(git status *), Bash(git merge-base *), Bash(git stash list), Bash(python -m pytest *), Bash(python -m grpc_tools.protoc *), Bash(pycodestyle *), Bash(make proto), Bash(uv lock --check), Bash(docker build *), Bash(gh pr view *), Bash(gh pr diff *)
---

# EvalBench code review

Review a change on four axes, in this order. **Verification comes first** — an
architecturally beautiful change that doesn't run is still broken, and running
the code surfaces real defects that reading alone will not.

1. **Does it work?** Prove it by execution, not by reading.
2. **Does it follow EvalBench architecture?** Registration wiring, base-class
   contracts, import style, isolation, concurrency, config-driven behavior, docs.
3. **Does it still ship?** The image builds, and GKE / Cloud Run / Cloud Build
   still work. A green local suite says nothing about any of these.
4. **Is it a good PR?** Reviewable size, single purpose, a description that
   answers what and why, and code that is clear, simple, concise, maintainable
   and idiomatic.

Do not skip phase 1 because a diff "looks obviously fine". Do not stop after
phase 1 because the tests pass — a change can pass every test and still be
wired into the wrong layer, break the container build, or be unreviewable.

## Phase 0 — Scope the diff

Establish exactly what changed before reading anything else.

```bash
BASE=$(git merge-base HEAD origin/main)
git diff --stat $BASE...HEAD
git diff -M $BASE...HEAD                 # -M so renames don't read as rewrites
git status --short                       # uncommitted work counts as part of the change
```

For a PR under review: `gh pr diff <n>` and `gh pr view <n>`.

### Size the change first — it sets how you review everything else

Defect detection degrades as diffs grow, so measure before you read:

```bash
git diff --shortstat $BASE...HEAD -- . \
  ':(exclude)uv.lock' ':(exclude)CHANGELOG.md' \
  ':(exclude)evalbench/evalproto/*_pb2*' ':(exclude)datasets/**/*.json'
```

Generated and vendored files are volume, not review surface — `uv.lock` alone
is 5,000 lines. Judge against the hand-written count:

- **Under ~400 lines** — review normally.
- **Over ~400 lines** — report it as a `pr-hygiene` finding with a concrete
  split proposal, naming the seams.
- **Any size** — pace at no more than ~500 lines per hour. If the change is
  larger than you can review at that rate, say which parts you read carefully
  and which you skimmed. Never imply uniform depth you didn't apply.

Then check whether formatting is mixed in with behavior:

```bash
git diff -w --shortstat $BASE...HEAD     # whitespace-insensitive
```

A whitespace-insensitive diff much smaller than the raw one means the PR
carries reformatting. Recommend splitting: functional and non-functional
changes are each trivial to review alone and opaque together.

Then classify the change, because it determines what "working" means:

| Change touches | Verify by |
|---|---|
| `evalbench/scorers/` | Unit test for the scorer + registration reachable from a run config |
| `evalbench/generators/models/` | Unit test with the CLI/SDK stubbed; never hit a live model in a test |
| `evalbench/evaluator/`, `evalbench/work/`, `evalbench/mp/` | Evaluator/orchestrator tests + reasoning about concurrency |
| `evalbench/databases/` | The dialect's test (may be skipped without creds — say so explicitly) |
| `evalbench/reporting/` | Round-trip a score through CSV; check BigQuery schema compatibility |
| `datasets/`, `docs/configs/`, `*.yaml` | Load the config and validate the schema; no Python tests will catch this |
| `pyproject.toml` | Pin has a comment explaining *why*; `uv.lock` regenerated; `docs/architecture.md` graph updated |
| `evalbench_service/`, `Makefile`, `cloudbuild.yaml`, `.dockerignore` | Phase 3 — build the image, dry-run the manifests |
| `docs/` only | Cross-check every claim against the code it documents |

State the classification in your report. If the diff spans several rows, all of
them apply.

## Phase 1 — Prove it works

Full commands, environment quirks and the known-failure baseline are in
[references/verification.md](references/verification.md). Read it before
running anything — this repo needs generated protos and a `PYTHONPATH` that CI
sets but a bare shell does not.

The short version:

```bash
make proto                                    # required; stubs are gitignored
cd evalbench && python -m pytest -q --ignore=evalbenchtest
pycodestyle evalbench --config=.pycodestyle   # from repo root
```

Then work through, in order:

1. **Run the tests that cover the change specifically.** Name them in the
   report. If `git diff` touched `scorers/trajectorymatcher.py`, then
   `test/trajectory_matcher_test.py` must have run and passed.
2. **Run the full suite** and compare against the baseline in
   references/verification.md. A *new* failure is a blocking finding. A
   pre-existing failure is not the author's problem — say which is which
   rather than reporting a red suite.
3. **Check the change is actually covered.** New behavior with no new or
   modified test is a finding, even when the suite is green. Confirm coverage
   by making the test fail: revert the source change (or mentally trace the
   assertion) and check the test would catch the regression. A test that passes
   both with and without the change tests nothing.
4. **Exercise the real path when tests can't.** Config, dataset and prompt
   changes are invisible to pytest. Load the YAML/JSON, validate required keys
   against `docs/configs/`, and for an agent change consider a one-scenario run
   with `runners: {agent_runners: 1}`. Never launch a long or paid eval run
   without asking the user first.
5. **Style gate.** `pycodestyle` with `.pycodestyle` is a required CI check
   (`.github/workflows/main.yml`). Report violations as blocking — they will
   fail the PR regardless of merit.

If you could not run something (missing GCP creds, no network, a paid API),
say so explicitly and mark that area unverified. Never imply you ran a check
you skipped.

## Phase 2 — Architecture conformance

Work through [references/architecture-checklist.md](references/architecture-checklist.md).
It has the specific contracts: the `Comparator.compare` positional signature,
the `QueryGenerator.generate_internal` rate-limit contract, the config-key
registration tables in `score.py` / `multi_trial_score.py` /
`generators/models/__init__.py`, the `from scorers import ...` import rule,
sandbox isolation, and the concurrency invariants that `mp/mprunner.py`
imposes.

The five failures that recur most in this repo, checked first:

- **Registered but not documented, or documented but not registered.** A new
  scorer needs both a branch in `scorers/score.py` keyed on its YAML name *and*
  a row in `docs/scorers.md`. A generator needs an entry in the `generators`
  dict in `generators/models/__init__.py`.
- **Wrong base-class hook.** Overriding `QueryGenerator.generate` instead of
  `generate_internal` silently bypasses rate limiting and retry.
- **Absolute imports.** `from evalbench.scorers import x` breaks under the
  `PYTHONPATH=$WORKSPACE/evalbench` layout CI uses. It must be
  `from scorers import x`.
- **Hardcoded projects, model IDs, regions or paths.** Everything
  environment-specific comes from the run config, model config, or the
  scenario's `env` block.
- **Escaping the sandbox.** Agent generators must stay inside the fake home —
  `.venv/fake_home*` locally, `/tmp_sessions/<session_id>/fake_home` when
  running under `eval_server.py`. Writing to the real `$HOME` contaminates the
  developer's machine locally and leaks across sessions in the container.

## Phase 3 — Container and cluster impact

The image is one artifact deployed three ways: GKE (gRPC eval server), Cloud
Run (viewer frontend + precompute jobs), and locally via `make container`. Unit
tests cover none of that. Work through
[references/deployment-checklist.md](references/deployment-checklist.md).

Always run these two — they are cheap, need no cluster, and catch the most
common breakage:

```bash
uv lock --check                                        # lockfile matches pyproject
docker build -f evalbench_service/Dockerfile -t evalbench-review .
```

`Dockerfile:34,36` run `uv sync --frozen`, so **a `pyproject.toml` edit without
a regenerated `uv.lock` fails the image build** while every test still passes.
That single check catches more deployment breakage than everything else here.

Then, if the diff touches anything in the table below:

| Diff touches | Ask |
|---|---|
| A new shelled-out binary | Is it in the Dockerfile's apt/npm install? Otherwise it exits 127 in the container. |
| A new absolute path | Created in the Dockerfile, mounted in `k8s/evalbench.yaml`, *and* in the Cloud Run `--add-volume-mount` flags? |
| A new env var | Added to `k8s/evalbench.yaml`, its `-test` twin, and the Cloud Run `--set-env-vars`? Does it have a safe default? |
| Startup, ports, processes | Traced through all three `entrypoint.sh` branches and the matching `supervisord_*.conf`? |
| Concurrency, memory, caching | Fits 20 CPU / 80Gi on GKE *and* 4 CPU / 8Gi on Cloud Run? |
| `k8s/*.yaml` | Was the parallel `*-test.yaml` updated too? `kubectl apply --dry-run=client` clean? |
| A new GCP API call | Needs an IAM grant on `evalbench@cloud-db-nl2sql.iam.gserviceaccount.com` — not inferable from the diff, so say it. |
| A scorer key or dataset used by `datasets/bat/example_run_config.yaml` | Cloud Build runs a real eval with that config and gates on `verifier/verify.py`. |
| `.git` access at runtime | `.dockerignore` excludes `.git`; `GitPython` code works locally and raises in the image. |

Two standing traps worth checking on any infra-adjacent change: the HPA scales
to 10 replicas while the PVC is `ReadWriteOnce` (extra replicas cannot
schedule), and `/tmp_sessions` does not exist on Cloud Run at all.

**Never run** `make deploy*`, `push*`, `undeploy*`, `redeploy*`, `run-*-job`, or
server-side `kubectl` during a review — they mutate the shared cluster and
registry. Name what would need to run and leave it to the user. If Docker isn't
available, report the build as unverified rather than assuming it passes.

## Phase 4 — PR hygiene and code health

Details and the measuring commands are in
[references/pr-quality.md](references/pr-quality.md). Two parts.

**Is the PR reviewable?** Size is already measured in Phase 0. Beyond that:

- **One change at a time.** A new scorer *and* a Dockerfile fix *and* a docs
  reorganization are three PRs. Name the seams if they're bundled.
- **The description answers what and why.** What issue does this fix, what
  related issues or PRs exist, why does it matter, and why this approach over
  the alternatives. This repo has no PR template, so nothing enforces it. The
  description is a historical record — someone running `git blame` in two years
  has no access to the ticket or the chat thread. `6a81e49` (#508) is the
  in-repo exemplar.
- **The title prefix drives the release.** `release-please-config.json` maps
  `feat:` → Features + minor bump, `fix:` → Bug Fixes + patch bump, and hides
  `chore:` / `docs:` from the changelog entirely. A behavior change titled
  `chore:` vanishes from the changelog permanently.

**Is the code healthy?** Five properties, each a question to ask of the diff:

| Property | Ask | Typical finding here |
|---|---|---|
| Clarity | Is the purpose and the *rationale* obvious to a reader who wasn't there? | A non-obvious constraint with no comment saying why. The `mcp>=1.8,<2` pin comment in `pyproject.toml` is the house standard. |
| Simplicity | Is this the simplest thing that works? | A config knob with one caller and no requester; a hand-rolled version of something already in `util/`. |
| Concision | High signal-to-noise? | Commented-out code, dead branches, `print()` instead of `logging`, comments restating the line below. |
| Maintainability | Can the next person change this safely? | `except Exception: pass` in a scorer — turns a bug into a silently wrong number. Hardcoded values that belong in config. |
| Consistency | Does it match the neighbours and Python idiom? | Missing Google-style `Args:`/`Returns:` docstrings or type hints where the module already uses them. |

Match the file you're editing first, the repo second, the
[Google style guide](https://google.github.io/styleguide/pyguide.html) third.

**Do not report line length.** `.pycodestyle` sets `max-line-length = 160` and
ignores `E402, E501, W503, W504`, deliberately overriding pyguide's 80. If
`pycodestyle` passes, formatting is not a finding.

## Phase 5 — Report

Rank findings by severity, most severe first, and report them with the
**ReportFindings** tool. Each finding needs a concrete failure scenario —
specific inputs or state leading to a specific wrong outcome — not a
description of the smell.

Use these categories:

- `broken` — verified failure: a test fails, style gate fails, or the code
  raises on a path you traced.
- `unverified` — the change works only under conditions you could not check;
  state what you couldn't run.
- `architecture` — violates a contract in the checklist. Cite the file and
  line of the contract, not just the offending line.
- `deployment` — breaks the image build, or breaks GKE / Cloud Run / Cloud
  Build. Name which of the three and what fails there.
- `coverage` — behavior with no test that would catch its regression.
- `docs` — `docs/` or `AGENTS.md` now contradicts the code.
- `pr-hygiene` — oversized, multi-purpose, formatting mixed with behavior, a
  description that doesn't answer what and why, or a title prefix that
  mislabels the release.
- `readability` — clarity, simplicity, concision, maintainability or
  consistency. Non-blocking unless it obscures correctness; prefix the summary
  with "Nit:" when it's a suggestion rather than a gate.

Before reporting, verify each finding against the actual code. Drop anything
you cannot substantiate — a plausible-sounding false positive costs the author
more time than a missed nit. If nothing survives verification, report an empty
list and say plainly what you ran and what passed.

Separate what blocks the merge from what doesn't, and cite a principle or an
in-repo precedent rather than preference — "this duplicates `util/rate_limit.py`"
is actionable, "I'd have done this differently" is not. Approve a change that
improves the codebase and has no blocking defects; don't hold it for style.

Close with a short verdict in prose: what you executed, the pass/fail counts
against baseline, whether the image built, the reviewable size of the diff,
what you could not verify, and whether the change is ready to merge. Do not
restate the findings in prose — ReportFindings already rendered them.

## Boundaries

- This is a review skill. Do not fix what you find unless the user asks; a
  review that rewrites the code destroys the author's ability to judge it.
- Judge the change the author made, not the change you would have made.
  Alternative designs belong in the verdict as a sentence, not as findings.
- Comment on the code, not the author: "this function does X", not "you did X".
- Security-sensitive diffs (credential handling, `util/auth.py`, `util/gcp.py`,
  anything shelling out with user-controlled input) warrant a follow-up
  `/security-review`; mention it rather than duplicating that skill's work.
