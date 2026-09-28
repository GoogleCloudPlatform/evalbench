# Containerized Eval Cases

By default an agentic run executes every scenario as a subprocess inside the
eval server pod: one sandbox home, one filesystem, one set of MCP servers,
shared by every scenario running concurrently. **Containerized execution** runs
each eval case in its own short-lived container instead, spread round-robin over
dedicated GKE worker (node) pools.

Supported for the **Claude Code** generator (`generator: claude_code`) only.
Other generators fall back to in-process execution.

---

## Table of Contents

- [Why](#why)
- [How it works](#how-it-works)
- [One-time cluster setup](#one-time-cluster-setup)
- [Run config reference](#run-config-reference)
- [What the container receives](#what-the-container-receives)
- [Getting results back](#getting-results-back)
- [Scaling to many concurrent runs](#scaling-to-many-concurrent-runs)
- [Eval server health and topology](#eval-server-health-and-topology)
- [Limitations](#limitations)
- [Troubleshooting](#troubleshooting)

---

## Why

| In-process (default) | Containerized |
|---|---|
| All scenarios share one sandbox home; concurrency needs `--fork-session` | One home per case, no session sharing |
| A scenario that corrupts its work dir or leaks a process affects its neighbours | Blast radius is one container |
| Concurrency is bounded by the eval server pod's CPU/memory | Concurrency is bounded by the worker pools, which autoscale |
| One bundled evalset is one `EvalGeminiCliRequest`, so `agent_runners` is effectively a no-op for it | The evalset is split back into one case per scenario |
| No per-case resource limits | `requests`/`limits` per case container |

---

## How it works

```
┌──────────────────── eval server pod (default node pool) ────────────────────┐
│  AgentOrchestrator                                                          │
│      └─ ContainerAgentEvaluator                                             │
│            ├─ splits the evalset into one EvalCaseSpec per scenario         │
│            ├─ WorkerPoolRouter  → round-robin pool pick, per-pool semaphore │
│            └─ KubernetesJobBackend → one Job per case                       │
└──────────────────────────────┬──────────────────────────────────────────────┘
                               │ ConfigMap (case spec) + Job
        ┌──────────────────────┴───────────────────────┐
        ▼                                              ▼
┌── evalbench-worker-pool-1 ──┐            ┌── evalbench-worker-pool-2 ──┐
│  Job: ebcase-cuj-01-...     │            │  Job: ebcase-cuj-02-...     │
│    case_runner.py           │            │    case_runner.py           │
│      AgentEvaluator         │            │      AgentEvaluator         │
│        ClaudeCodeGenerator  │            │        ClaudeCodeGenerator  │
│        scorers              │            │        scorers              │
│    result → pod logs        │            │    result → pod logs        │
└─────────────────────────────┘            └─────────────────────────────┘
```

1. `AgentOrchestrator` reads `containerization:` from the run config. When it is
   disabled or absent, nothing changes — it builds the usual `AgentEvaluator`.
2. `ContainerAgentEvaluator` flattens the dataset into one scenario per case and
   packages each as an `EvalCaseSpec`: the scenario, a stripped copy of the run
   config, and the model YAMLs the config points at, inlined.
3. `WorkerPoolRouter` hands each case a pool. A pool that is at
   `max_concurrent` is skipped rather than blocking; when every pool is
   saturated the case queues on the round-robin pick.
4. `KubernetesJobBackend` writes the spec as a ConfigMap, creates a Job pinned to
   that pool, polls it, and reads the result out of the pod logs.
5. **Setup and teardown scripts still run once per run, in the eval server** —
   they are stripped from the child config so N containers do not race to
   create and drop the same fixtures.

The whole eval case runs in the container: every Claude Code turn, the
simulated-user loop, and scoring. Only the result rows come back.

---

## One-time cluster setup

The worker pools live in the existing cluster; the eval server keeps scheduling
on the default pool.

```bash
POOL_COUNT=2 evalbench_service/k8s/worker_pools.sh create
```

This creates `evalbench-worker-pool-1..N`, each:

- autoscaled `MIN_NODES=0` → `MAX_NODES=10` (an idle pool costs nothing; the
  first case of a run pays a node cold start)
- tainted `evalbench.io/dedicated=eval-worker:NoSchedule`, so only eval-case
  Jobs — which carry the matching toleration — land there
- labelled `evalbench.io/worker-pool=<name>`, with GKE's own
  `cloud.google.com/gke-nodepool=<name>` label used for the `nodeSelector`
- running with `--workload-metadata=GKE_METADATA` for Workload Identity

It then applies [`worker_rbac.yaml`](/evalbench_service/k8s/worker_rbac.yaml),
which grants the `evalbench-ksa` service account the permissions the dispatcher
needs: create/get/list/delete `jobs`, create/get/patch/delete `configmaps`
(`patch` sets each ConfigMap's owner to its Job), and get `pods/log`.

Other subcommands:

```bash
evalbench_service/k8s/worker_pools.sh list     # pools and their nodes
evalbench_service/k8s/worker_pools.sh delete   # tear the pools down
DRY_RUN=1 evalbench_service/k8s/worker_pools.sh create   # print, don't run
```

Everything is overridable by environment variable — `PROJECT`, `CLUSTER`,
`ZONE`, `POOL_PREFIX`, `POOL_COUNT`, `MACHINE_TYPE`, `DISK_SIZE`, `MIN_NODES`,
`MAX_NODES`, `TAINT_KEY`, `TAINT_VALUE`, `NAMESPACE`, `KSA`.

**Spread pools across zones.** A single zone can stock out of a machine type
(the prototype hit this with `n2-standard-64` in `us-central1-c`), and then the
autoscaler cannot add nodes at all. Set `NODE_LOCATIONS` to spread each pool:

```bash
NODE_LOCATIONS=us-central1-a,us-central1-b,us-central1-f \
TOTAL_MAX_NODES=30 \
  evalbench_service/k8s/worker_pools.sh create
```

With `NODE_LOCATIONS` set the bounds are pool-wide (`TOTAL_MIN_NODES` /
`TOTAL_MAX_NODES`, defaulting to `MIN_NODES` / `MAX_NODES`) and
`LOCATION_POLICY` defaults to `ANY`, so the autoscaler takes capacity wherever
it exists rather than insisting on balance. Existing pools are skipped, not
modified — delete and recreate them (or use
`gcloud container node-pools update --node-locations`) to change zones.

The node pools are cluster-wide, but the dispatcher's Role is namespaced. To
dispatch from the test deployment instead, re-target both together:

```bash
NAMESPACE=evalbench-test-namespace KSA=evalbench-test-ksa \
  evalbench_service/k8s/worker_pools.sh create
```

and set the matching `namespace` / `service_account` in the run config.

---

## Run config reference

Add a `containerization:` block. A complete working example lives at
[`datasets/claude-code-tools/example_run_containerized_config.yaml`](/datasets/claude-code-tools/example_run_containerized_config.yaml).

```yaml
containerization:
  enabled: true
  backend: gke
  image: us-central1-docker.pkg.dev/cloud-db-nl2sql/evalbench/eval_server:latest
  namespace: evalbench-namespace
  service_account: evalbench-ksa
  worker_pools:
    - evalbench-worker-pool-1
    - evalbench-worker-pool-2
  max_concurrent_per_pool: 4
  resources:
    requests: {cpu: "2", memory: "8Gi"}
    limits:   {cpu: "4", memory: "16Gi"}
  secrets:
    - name: evalbench-sa-key
      mount_path: /etc/evalbench-sa-key
      optional: true
  job_timeout: 45m
  keep_failed_jobs: true

eval_case_timeout: 30m
```

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `false` | Off by default; absent block behaves the same. |
| `backend` | `gke` | Only `gke` (aliases `k8s`, `kubernetes`) is implemented. |
| `image` | — | Required. The image case containers run; falls back to `$EVALBENCH_CASE_IMAGE`. Use the eval server image — it already carries node, npm, gcloud and uv. |
| `namespace` | `evalbench-namespace` | Namespace for the Jobs and ConfigMaps. |
| `service_account` | `evalbench-ksa` | KSA the case pods run as (Workload Identity). |
| `worker_pools` | — | Required, non-empty, no duplicates. Each entry is a pool name, or a mapping with `name`, `max_concurrent`, `resources`, `node_selector`. |
| `max_concurrent_per_pool` | `8` | Per-pool concurrency; total in-flight cases is the sum over pools. |
| `resources` | `{}` | Default `requests`/`limits`, overridable per pool. |
| `env` | `{}` | Extra env vars for every case container. Wins over `inherit_env`. |
| `inherit_env` | `EVAL_GCP_PROJECT_ID`, `EVAL_GCP_PROJECT_REGION`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | Orchestrator env vars copied into each case container when set. Set to `[]` to disable. Config only, never credentials — Job specs are readable by anyone with `get jobs`. |
| `secrets` | `[]` | Secrets to mount. Each needs `name` and `mount_path`; optional `read_only`, `default_mode`, `optional`. |
| `command` | `python /evalbench/evalbench/container/case_runner.py` | Container entrypoint. Run by path so the flat intra-package imports resolve, exactly as supervisord launches `eval_server.py`. |
| `image_pull_policy` | `Always` | |
| `poll_interval_seconds` | `5` | Job status poll interval. |
| `ttl_seconds_after_finished` | `900` | Kubernetes TTL on finished Jobs. |
| `job_timeout` | `eval_case_timeout` + 10m | Job `activeDeadlineSeconds`. Accepts duration strings (`45m`, `1h`). The grace covers image pull, startup, and result emit — work the per-case timeout does not account for. |
| `pool_selector_label` | `cloud.google.com/gke-nodepool` | The node label the `nodeSelector` pins on. |
| `taint_key` / `taint_value` | `evalbench.io/dedicated` / `eval-worker` | Must match the pools' taint. |
| `keep_failed_jobs` | `true` | Keep failed Jobs and their ConfigMaps for `kubectl describe`. Successful ones are always deleted. |

`runners.agent_runners` is ignored under containerization — there is one
scenario per container already — and forced to `1` in the child config.

---

## What the container receives

The case spec is a flat set of small text files, mounted read-only at
`/etc/evalbench-case`:

| File | Contents |
|---|---|
| `config.yaml` | The run config, minus `containerization`, `dataset_config`, `set_up_script` and `tear_down_script`, with model paths rewritten. |
| `scenario.json` | The single scenario, with `resolved_work_dir` re-rooted under `/tmp/evalbench-work/<case>`. |
| `meta.json` | `case_id`, `job_id`, `run_time`, the env-file manifest. |
| `model.N.yaml` | Every model YAML the config references — top-level *and* scorer-level (`goal_completion`, `behavioral_metrics`, …) — inlined and deduped. |
| `envfile.*` | The scenario's declared `env_files`, materialized into the sandbox home before the run. |

The container also gets `EVALBENCH_CASE_DIR`, `EVALBENCH_JOB_ID`,
`EVALBENCH_CASE_ID` and `EVALBENCH_WORKER_POOL` in its environment.

Because the spec travels as a ConfigMap it is capped at **1 MiB**. The backend
warns past 700 KiB and refuses to submit past 1 MiB — trim the scenario or bake
its large inputs into the image.

---

## Getting results back

The result comes home in the pod's **stdout**, between sentinels:

```
===EVALBENCH_CASE_RESULT_BEGIN===
{"case_id": "...", "agent_results": [...], "scoring_results": [...], "error": null}
===EVALBENCH_CASE_RESULT_END===
```

Pod logs rather than a shared volume, because the cluster's PVC is
`ReadWriteOnce` (`premium-rwo`) and cannot be mounted across nodes. Hence the
one discipline `case_runner.py` enforces: **all logging goes to stderr**, so
stdout carries nothing but the payload.

A case that produces no payload does not vanish. The evaluator synthesizes a
failure row with the same shape as a normal result, carrying `worker_pool`,
`container_ref`, and whatever the backend could learn about why — pod phase,
unmet conditions, `waiting`/`terminated` reasons — so unschedulable pods and
image-pull failures show up in reporting instead of as a silently short run.
A case whose scenario raised inside `AgentEvaluator` (which yields no rows) is
reported the same way rather than silently dropped.

### Sandbox artifacts (agent trajectories)

In-process runs leave each scenario's sandbox home (`fake_home`) on the eval
server, where the `gcs_artifacts` reporter zips it at reporting time. A case
pod is gone by then, so **the case runner uploads its own sandbox** before
emitting the result, whenever the run config has:

```yaml
reporting:
  gcs_artifacts:
    bucket: evalbench-sessions-cloud-db-nl2sql
    path_prefix: results   # optional, defaults to `results`
```

- Same exclusions (hidden files, `node_modules`, `.venv`, …) and the same
  object layout as the reporter: `gs://<bucket>/<path_prefix>/<job_id>/<eval_id>.zip`.
- The row's `artifact_uri` column holds that URI. Failed and crashed cases
  upload too, and their failure rows carry `artifact_uri`, since those are the
  ones worth debugging.
- The row's `fake_home` is cleared (it is a path inside the deleted pod), so
  the eval-server reporter does not upload a second copy.
- Upload failures are logged and never fail the case. `delegated: true`
  disables the upload, as it does for the reporter.
- The case pod uploads with its own identity (`service_account` / mounted
  `secrets:`), which therefore needs `storage.objects.create` on the bucket.

---

## Parity with the eval server pod

A case pod is **not** a copy of the eval server pod. It shares the cluster, the
VPC and the GCP service account, so anything that depends on network egress or
IAM behaves the same. Everything pod-level has to be declared:

| Eval server has | Case pod gets |
|---|---|
| `/tmp` emptyDir | Same — created automatically. |
| `/tmp_sessions` (ReadWriteOnce PVC) | **Nothing.** The generator falls back to a sandbox home inside the container, which is what you want per-case anyway. |
| `/tmp_session_files` (GCS Fuse) | **Nothing.** Reporting still runs in the eval server, which keeps its mount, so results land as usual. Do not add the Fuse volume to a Job without setting the sidecar to terminate, or the Job will never complete. |
| `/etc/evalbench-sa-key` (Secret) | Only via `secrets:`. |
| `EVAL_GCP_PROJECT_ID`, `EVAL_GCP_PROJECT_REGION`, `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION` | Copied automatically by `inherit_env`. |
| `EVAL_DB_PASSWORD` and any other Deployment env | Only via `env:`. |
| 20 CPU / 80 GiB | Whatever `resources` says — check it against the pool's machine type. |

The one that bites first is env: the simulated user and every LLM judge now run
*inside* the case container, and `util.gcp` resolves their Vertex project and
region from `EVAL_GCP_PROJECT_ID` / `EVAL_GCP_PROJECT_REGION` whenever the model
YAML does not name them. That is why those are inherited by default.

## Scaling to many concurrent runs

Two limits are process-wide, not per run, and both matter once a CI fan-out
points hundreds of runs at one eval server.

**Per-pool capacity is shared by every eval session.** `max_concurrent_per_pool`
caps the *cluster*, not one session's view of it. The eval server handles many
`Eval` RPCs at once and each builds its own evaluator; if each owned its own
semaphores, N sessions would each dispatch a full pool's worth of cases and the
autoscaler would be asked for N times the nodes. The slots therefore live in a
registry keyed by `(namespace, pool)`. The first session to configure a pool
fixes its capacity — a later run asking for a different number is warned and
gets the existing limit, since taking the larger of the two would defeat the cap.
Changing it means restarting the eval server.

So total in-flight cases across the whole cluster is:

```
sum over pools of max_concurrent(pool)
```

regardless of how many runs are in flight. Size it against the pools:
`MAX_NODES x (allocatable CPU per node / resources.requests.cpu)`. Asking for
more than the pools can hold just leaves Jobs `Pending` and burning their
`activeDeadlineSeconds`.

**The eval server caps concurrent runs at ~32 by default.** `Eval` hands the
whole evaluation to the event loop's default executor, so each in-flight RPC
holds one thread for the entire run. CPython sizes that pool at
`min(32, cpu_count + 4)` — which a caller that drives *one scenario per RPC*
hits long before the pod runs out of CPU. `EVALBENCH_EVAL_THREADS` on the
Deployment lifts it; the checked-in manifests set it to 128, the value the
prototype load test ran at:

```yaml
- name: EVALBENCH_EVAL_THREADS
  value: "128"
```

> [!WARNING]
> Only raise this when containerization is on. The threads are then just
> polling Kubernetes Jobs and are nearly idle. Without containerization each
> one runs a full agent CLI, its MCP servers and the scorers on the eval server
> pod, and oversubscribing only thrashes it.

The backend itself is shared too: one Kubernetes API client per
`(backend, namespace)`, refcounted so the last session out closes it rather
than the first one to finish.

**Status polling is one LIST per interval, not one GET per case.** With 500
cases in flight, per-case polling would be 100 GETs a second against the API
server at the default 5s interval. Instead every Job carries an
`evalbench.io/dispatcher=<eval server pod name>` label, and the first waiter to
find the snapshot older than `poll_interval_seconds` refreshes it with a single
paged, label-selected LIST; everyone else reads from it. A Job missing from a
snapshot taken after it was created is confirmed with one direct GET (a `404`
means it was reaped and the case failed). If the LIST itself fails, waiters
fall back to per-Job GETs until it recovers.

**Transient API errors are retried.** `429`, `5xx` and connection errors get up
to five attempts with jittered exponential backoff capped at 8s (honouring
`Retry-After`). A create that gets `409 AlreadyExists` on a *retry* is taken as
the earlier attempt having landed; on the first attempt it is still an error.

---

## Eval server health and topology

The eval server runs as a **singleton**. `/tmp_sessions` is a
`ReadWriteOnce` PVC, so a second replica cannot mount it: it would sit in
`ContainerCreating` forever and — with a `RollingUpdate` strategy — wedge every
rollout behind it. The manifests therefore pin:

- `strategy: Recreate` on the Deployment — the old pod releases the volume
  before the new one starts.
- `minReplicas: 1` / `maxReplicas: 1` in [`hpa.yaml`](/evalbench_service/k8s/hpa.yaml).

Horizontal scale comes from the worker pools, not from more eval servers.

The server exposes plain-HTTP probe endpoints next to gRPC, served from their
own thread rather than the gRPC event loop:

| Path | Returns |
|---|---|
| `/healthz`, `/livez` | `200` unless the gRPC event loop has not ticked for `EVALBENCH_LOOP_STALL_SECONDS`, then `503`. |
| `/readyz` | `200` once the gRPC server has started, `503` before that and while draining. |

| Env var | Default | Meaning |
|---|---|---|
| `EVALBENCH_HEALTH_PORT` | `8080` | Port for the probe endpoints. `0` disables them. Skipped if it collides with the gRPC `PORT` (Cloud Run sets `PORT=8080`). |
| `EVALBENCH_LOOP_STALL_SECONDS` | `300` | How long the event loop may go without a heartbeat before liveness fails. |
| `EVALBENCH_SHUTDOWN_GRACE_SECONDS` | `5` | How long in-flight RPCs get after `SIGTERM` or `Ctrl-C`. |

**Liveness means "wedged", not "busy".** Sessions live in the server's memory,
so a restart fails every in-flight eval. In a 100-run load test, an earlier
version served the probes on the gRPC loop with a 10s timeout; when ~30 runs
finished at once the loop was too busy to answer, the kubelet restarted the
pod, and 22 runs failed with `Session not configured`. So the endpoint now has
its own thread, the loop refreshes a heartbeat every second, and `/healthz`
fails only once that heartbeat is minutes old. The server logs
`health: event loop lagged Ns` whenever the loop wakes up 5s or more late —
the early warning that a handler is blocking it.

On `SIGTERM` the server flips `/readyz` to `503` (so the Service stops routing
to it) and then stops gRPC with the grace period above.

**Nothing outlives its owner.** Each case Job is owned by the eval server pod
that created it (`EVALBENCH_POD_NAME` / `EVALBENCH_POD_UID` /
`EVALBENCH_POD_NAMESPACE`, set through the downward API), and each case
ConfigMap by its Job. So when the eval server pod is deleted mid-run — a
rollout, an eviction — Kubernetes garbage-collects its in-flight Jobs rather
than letting them burn their full deadline, and a failed Job kept by
`keep_failed_jobs` takes its ConfigMap with it when the TTL reaps it. Pod
ownership is skipped when the eval server runs in a different namespace from
the Jobs (ownerReferences cannot cross namespaces) or outside the cluster; the
Jobs then rely on their TTL alone.

---

## Limitations

- **Claude Code only.** `ContainerAgentEvaluator` rejects any other generator.
  It reads the generator name straight out of the model YAML rather than
  instantiating the generator, which would set up a sandbox home and start MCP
  servers in the orchestrator pod.
- **Work-dir contents are not shipped.** `resolved_work_dir` is re-rooted, but
  the files in it stay on the orchestrator. A scenario that depends on fixture
  files needs them baked into the case image or mounted into the pools; the
  spec builder warns loudly when it re-roots a non-empty work dir.
- **Setup/teardown are run once per run, not per case**, by the eval server —
  so cases still share whatever database fixtures those scripts create.
- **One-shot Jobs.** `backoffLimit: 0`: a retried eval case would double-charge
  tokens and report a run nobody asked for.
- **Client cancellation is not propagated.** If the caller of `Eval` gives up
  (deadline, disconnect), the eval server keeps waiting on that run's Jobs
  until they finish or hit their deadline. Only deleting the eval server pod
  cancels them early.

---

## Troubleshooting

**Jobs stay `Pending` and cases fail with `Unschedulable`.** The pools scale
from zero; the first case waits on a node. If it never schedules, check the
taint matches (`taint_key`/`taint_value` vs. what `worker_pools.sh` applied) and
that `resources.requests` fits the pool's `MACHINE_TYPE`.

```bash
kubectl -n evalbench-namespace get jobs -l app=evalbench-eval-case
kubectl -n evalbench-namespace describe job <job-name>
```

**`Case container produced no result payload`.** The container ran but emitted
nothing parseable. With `keep_failed_jobs: true` the Job is still there:

```bash
kubectl -n evalbench-namespace logs job/<job-name>
```

**`forbidden: cannot create resource "jobs"`.** RBAC was not applied; re-run
`kubectl apply -f evalbench_service/k8s/worker_rbac.yaml`.

**Auth failures inside the case container.** Case pods inherit none of the eval
server's volumes. Either rely on Workload Identity through `evalbench-ksa`, or
mount the key explicitly via `secrets:` — Claude Code reads
`/etc/evalbench-sa-key/key.json`.

**Logs are missing after a run.** `ttl_seconds_after_finished` (15m default)
reaps finished Jobs and their pods. Raise it if you need a longer window. Kept
failed Jobs are also garbage-collected as soon as the eval server pod that
created them is deleted, e.g. by a rollout.

**`could not make job ... own its ConfigMap`.** The Role predates the `patch`
verb on `configmaps`; re-apply `worker_rbac.yaml`. Cases still run — only the
leak protection for kept failed Jobs is lost.

**Finding one eval server's Jobs.** Filter by dispatcher:

```bash
kubectl -n evalbench-namespace get jobs \
  -l app=evalbench-eval-case,evalbench.io/dispatcher=<eval-server-pod-name>
```

---

## Related

- [Agentic evaluations](/docs/agentic-evals.md) — execution model and sandboxing.
- [Claude Code](/docs/claude_code_agent_testing.md) — generator setup and config.
- [Run config](/docs/configs/run-config.md) — the rest of the run config.
