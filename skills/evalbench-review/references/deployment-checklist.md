# Container & cluster checklist

EvalBench ships as one image (`evalbench_service/Dockerfile`) that runs in three
different shapes. A change that works from a local checkout can still break the
build, the GKE deployment, or the Cloud Run services — and none of the unit
tests will tell you.

## The three runtime modes

`evalbench_service/entrypoint.sh` branches on environment:

| Mode | Detected by | supervisord conf | Processes started |
|---|---|---|---|
| GKE | `KUBERNETES_SERVICE_HOST` is set | `supervisord_evalbench.conf` | `evalbench/eval_server.py` only |
| Cloud Run | `CLOUD_RUN == "True"` | `supervisord_cloudrun.conf` | viewer frontend + `viewer/run_precompute.py` — **no gRPC eval server** |
| Local / container | neither | `supervisord_combined.conf` | frontend + eval server + precompute, `PRECOMPUTE_INTERVAL=30` |

Consequences to check:

- A change to startup, ports, or process lifecycle must be traced through **all
  three** branches. Working under `make container` (combined mode) proves
  nothing about GKE.
- A new long-running process needs a `[program:]` block in each conf where it
  belongs — adding it only to `supervisord_combined.conf` means it never runs
  in production.
- Code reachable only from the frontend still runs on GKE if it's imported at
  module load. Cloud Run has no eval server, so eval-server-only work silently
  never happens there.
- `eval_server.py:63,80` binds localhost when `CLOUD_RUN` is set. Changing bind
  behavior affects Cloud Run ingress.

## Does the image still build?

The single most common deployment breakage in this repo:

> `Dockerfile:34,36` run `uv sync --frozen --all-packages`. **`--frozen` fails
> the build if `uv.lock` doesn't match `pyproject.toml`.** Every dependency edit
> must come with a regenerated lockfile.

Cheapest check, no cluster needed:

```bash
uv lock --check                                       # lockfile is in sync
docker build -f evalbench_service/Dockerfile -t evalbench-review .
```

`make build` does the same plus stamps `viewer/version.txt` from git. The
Makefile falls back to Podman when Docker is absent (`CONTAINER_ENGINE`).

Things that break the build or the image, in rough order of frequency:

- **Lockfile drift** — as above.
- **New workspace member.** `Dockerfile:32-33` copies `pyproject.toml`,
  `uv.lock` and `viewer/pyproject.toml` *before* `COPY . .`, so a new member
  under `[tool.uv.workspace]` needs its own `COPY` line in that early layer or
  the first `uv sync` fails.
- **A new external binary.** The image installs a fixed set:
  `unzip git wget ca-certificates gnupg curl vim jq supervisor make python3
  python3-pip sudo`, Node 20 with a global `@dataform/cli`, and
  `google-cloud-cli`. A scorer or generator that shells out to anything else
  works locally and exits 127 in the container. Add it to the apt/npm line.
- **Proto changes.** `Dockerfile:43` runs `uv run make proto -f ./Makefile`;
  a `.proto` that only compiles with a locally-installed plugin breaks here.
- **Anything reading `.git` at runtime.** `.dockerignore` excludes `.git`,
  `.venv`, `results`, `__pycache__`, `.pytest_cache`. `GitPython` is a declared
  dependency — code using it against the repo works locally and raises
  `InvalidGitRepositoryError` in the image.
- **Image-level env assumptions.** The Dockerfile sets
  `PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION=python`,
  `PYTHONPATH=/evalbench/evalbench/evalproto:.`, `HOME=/root`,
  `VIRTUAL_ENV=/evalbench/.venv`, `PATH`, `CLOUDSDK_CONFIG`, and
  `AGY_CLI_DISABLE_AUTO_UPDATE=true`. Note the image `PYTHONPATH` differs from
  CI's `evalbench:.` and depends on the working directory being `/evalbench` —
  code that changes cwd breaks imports.
- **Root vs `claudeuser`.** The image creates a non-root `claudeuser` because
  Claude Code refuses `--dangerously-skip-permissions` as root, while GKE runs
  the pod as `runAsUser: 0`. `claude_code.py:53` keys off `os.getuid() == 0` to
  chown the sandbox. A change to sandbox paths or permissions must keep that
  chown correct or Claude Code runs fail only in the container.
- **`agy` is installed per session at runtime**, not at build time — deliberate,
  see the Dockerfile comment. Don't "fix" it by baking in a version.

## Paths that exist only in a container

Two absolute paths are hardcoded in application code and satisfied entirely by
mounts:

| Path | Defined at | GKE | Cloud Run | Local |
|---|---|---|---|---|
| `/tmp_sessions/` | `util/sessionmgr.py:8` (`SESSION_RESOURCES_PATH`) | PVC `evalbench-ssd-pvc`, `premium-rwo` SSD, 1000Gi | **not mounted** | created by Dockerfile |
| `/tmp_session_files` | `reporting/csv.py:13`, all `viewer/*` readers | GCS Fuse CSI, bucket `evalbench-sessions-cloud-db-nl2sql` | Cloud Storage volume, same bucket | created by Dockerfile |

Agent sandboxes move with the runtime: when `sys.argv[0]` ends with
`eval_server.py` the generators use `/tmp_sessions/<session_id>/fake_home`,
otherwise `.venv/fake_home*` (see `gemini_cli.py:33-41`, `claude_code.py:35-44`,
`codex_cli.py:69`, `agy_cli.py:107`). Review sandbox changes against both.

Rules:

- A new hardcoded absolute path must be created in the Dockerfile **and**
  mounted in `evalbench_service/k8s/evalbench.yaml` **and** added to the Cloud
  Run `--add-volume-mount` flags — or it silently lands on ephemeral container
  disk in one of the three modes.
- The root filesystem is writable (`readOnlyRootFilesystem: false`) but
  ephemeral and shared with the node. Large artifacts belong on the PVC or GCS,
  not `/evalbench/results`.
- The gcsfuse mount requires the pod annotation `gke-gcsfuse/volumes: "true"`;
  dropping it makes the pod fail to start.
- Anything relying on `SESSION_RESOURCES_PATH` does not persist on Cloud Run.

## GKE manifests

Under `evalbench_service/k8s/`: `namespace.yaml`, `pvc.yaml`, `ksa.yaml`,
`service.yaml`, `evalbench.yaml`, `hpa.yaml`, `vertical-autoscale.yaml`, plus
`mesh.yaml` / `grpc_route.yaml`, and a parallel `*-test.yaml` set.

- **Prod and test manifests must move together.** `make deploy-test` applies
  `namespace-test`, `ksa-test`, `service-test`, `evalbench-test`,
  `vertical-autoscale-test` — no HPA, no PVC. A change to `evalbench.yaml` that
  skips `evalbench-test.yaml` leaves the test environment diverged.
- **Capacity.** Requests equal limits at **20 CPU / 80Gi**. Raising default
  `runners:` concurrency, per-scenario memory, or adding an in-memory cache is a
  capacity change against that ceiling — flag it explicitly.
- **The HPA/PVC conflict.** `hpa.yaml` scales 1→10 replicas on 50% CPU, but
  `pvc.yaml` is `ReadWriteOnce`. A second replica cannot mount the volume and
  will not schedule. Treat any change that increases replica count, or that
  assumes state in `/tmp_sessions` is shared across replicas, as broken —
  sessions are node-local.
- **Ports.** The container declares 50051 (grpc), 8000 (metrics), 3000 (ui);
  `service.yaml` exposes only 50051 and 3000, with a NEG annotation for
  directpath and `RequireDualStack` IPv6-first. A new port needs the container
  port, the Service port, and possibly `grpc_route.yaml` / `mesh.yaml`.
- **Identity.** Workload Identity via the `ksa.yaml` annotation
  (`evalbench@cloud-db-nl2sql.iam.gserviceaccount.com`), plus a read-only
  secret mounted at `/etc/evalbench-sa-key`. A new GCP API call needs an IAM
  role grant on that service account — call it out, since it can't be inferred
  from the diff. Adding new credentials to env or into the image is a finding;
  `EVAL_DB_PASSWORD` is already a plaintext manifest value and should not be a
  precedent.
- **New required env vars** must be added to `evalbench.yaml`, its `-test`
  twin, and the Cloud Run `--set-env-vars` in the Makefile, then documented.
  Code reading a new var without a default breaks the pod at startup.
- **VPA.** `vertical-autoscale.yaml` runs alongside explicit requests/limits —
  check for conflict when changing resources.
- Deployment is manual: `make build && make push && make redeploy`. A merged
  code change is not live until someone runs those. `imagePullPolicy: Always`
  with the `:latest` tag means `make redeploy` (rollout restart) is what picks
  up a new image.

## Cloud Run

`make deploy-corprun`, `create-precompute-job`, `create-recompute-job` — image
`us-central1-docker.pkg.dev/evalbench-dev/cr-images/eval_server:latest`.

- **4 CPU / 8Gi**, versus GKE's 20/80Gi. Work sized for the cluster OOMs here.
- `--port=3000`, `CLOUD_RUN=True`, `MESOP_XSRF_CHECK=false`, `min-instances=1`,
  `--no-cpu-throttling`, internal ingress on `cr-infra-vpc-network`.
- GCS volume at `/tmp_session_files`; no PVC.
- The jobs run `python3 viewer/precompute_trends.py` (and `--clean` for
  recompute). Changing that script's CLI surface or entry point requires
  recreating the jobs — `gcloud run jobs create` is not idempotent and will
  fail against an existing job.

## Cloud Build

`cloudbuild.yaml` is a fuller gate than the GitHub Actions workflows:

1. `uv sync` + proto + `pytest evalbench/test` with `PYTHONPATH=evalbench:.`
   and `SKIP_CLOUD_TESTS=true` (which skips `bigtable_test.py` and
   `spanner_test.py` — so those get *less* coverage here, not more).
2. `docker build -f evalbench_service/Dockerfile`.
3. A **real eval run** inside the built image:
   `EVAL_CONFIG=datasets/bat/example_run_config.yaml`, `evalbench/run.sh`.
4. `verifier/verify.py` against the shared `eval_results` volume.

So breaking `datasets/bat/example_run_config.yaml` or the thresholds in
`verifier/verify.py` breaks the pipeline even with a green unit suite. Renaming
or removing a scorer key that config depends on is exactly this failure.

## What to run during a review

Safe and worth doing:

```bash
uv lock --check
docker build -f evalbench_service/Dockerfile -t evalbench-review .
kubectl apply --dry-run=client -f evalbench_service/k8s/<changed>.yaml   # client-side only
```

Optional smoke test of the built image: `make container` (binds 3000/50051 and
mounts `~/.config/gcloud`), or `make shell` for an interactive poke.

**Never run during a review:** `make deploy`, `deploy-test`, `deploy-corprun`,
`push`, `push-test`, `push-corprun`, `undeploy*`, `redeploy*`, `run-*-job`, or
any server-side `kubectl`. They mutate the shared cluster and the shared
registry. Describe what would need to run and let the user decide.

If Docker/Podman isn't available on the machine, say the build was not verified
rather than assuming it's fine — lockfile drift is invisible any other way.
