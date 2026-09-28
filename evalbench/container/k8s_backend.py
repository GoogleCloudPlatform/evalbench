"""Runs each eval case as a Kubernetes Job pinned to a worker (node) pool.

The orchestrator pod stays where it is; every eval case becomes a short-lived
Job whose pod is placed on one of the dedicated worker pools created by
`evalbench_service/k8s/worker_pools.sh`. The case spec travels as a ConfigMap
mounted into the pod, and the result travels back through the pod's logs as a
sentinel-delimited JSON blob -- the cluster's PVC is ReadWriteOnce, so a shared
filesystem is not available across nodes.

Production concerns handled here, beyond the basic submit/poll/collect loop:

- **API load.** Hundreds of in-flight cases must not mean hundreds of GETs per
  poll interval. Waiters share one label-selected LIST per interval
  (`_JobStatusCache`) and only fall back to a per-Job GET to confirm a Job
  that has vanished from the LIST.
- **Leaks.** Every Job is owned by the eval server pod that created it, and
  every ConfigMap by its Job, so Kubernetes garbage collection cleans up after
  a dispatcher that crashed mid-run and after `keep_failed_jobs` once the TTL
  reaps the Job.
- **Transient API errors.** 429/5xx and connection errors are retried with
  bounded, jittered exponential backoff; a create that hits 409 on a retry is
  treated as the earlier attempt having landed.
"""

from dataclasses import dataclass
from typing import Callable, Optional
import logging
import os
import random
import socket
import threading
import time

from container.backend import (
    CaseHandle,
    CaseResult,
    ContainerBackend,
    extract_result_payload,
)
from container.config import ContainerizationConfig, WorkerPool
from container.spec import CASE_DIR, EvalCaseSpec, sanitize_case_id

# Kubernetes object names are DNS-1123 labels: 63 chars max.
_MAX_NAME_LEN = 63
_NAME_PREFIX = "ebcase"

# ConfigMaps are capped at 1 MiB; warn well before the API rejects the create.
_CONFIGMAP_WARN_BYTES = 700 * 1024
_CONFIGMAP_MAX_BYTES = 1024 * 1024

APP_LABEL = "evalbench-eval-case"
DISPATCHER_LABEL = "evalbench.io/dispatcher"

# Page size for the shared status LIST.
_LIST_PAGE_SIZE = 500

# Retries for transient API failures.
_RETRYABLE_STATUSES = frozenset({429, 500, 502, 503, 504})
_MAX_ATTEMPTS = 5
_BACKOFF_BASE_SECONDS = 0.5
_BACKOFF_MAX_SECONDS = 8.0

# The case runner prints its payload last and on a single line, so a short
# tail almost always holds it; the full log is only fetched when it does not.
_LOG_TAIL_LINES = 200
# How much log text a CaseResult keeps. Nothing downstream needs more than the
# end of the log, and hundreds of in-flight results each holding a full
# Claude Code transcript add up.
_MAX_RESULT_LOG_CHARS = 64 * 1024

_SA_NAMESPACE_FILE = "/var/run/secrets/kubernetes.io/serviceaccount/namespace"


# -- identity --------------------------------------------------------------


def dispatcher_id(environ: Optional[dict] = None) -> str:
    """A label-safe id for this eval server process's Jobs.

    The pod name when running in the cluster (set via the downward API), the
    hostname otherwise -- which is also the pod name, but the env var is
    explicit about intent.
    """
    environ = os.environ if environ is None else environ
    raw = environ.get("EVALBENCH_POD_NAME") or socket.gethostname()
    return sanitize_case_id(raw, max_len=63)


def _pod_namespace(environ: dict, namespace_file: str) -> Optional[str]:
    namespace = environ.get("EVALBENCH_POD_NAMESPACE")
    if namespace:
        return namespace
    try:
        with open(namespace_file) as f:
            return f.read().strip() or None
    except OSError:
        return None


def owner_pod(
    job_namespace: str,
    environ: Optional[dict] = None,
    namespace_file: str = _SA_NAMESPACE_FILE,
) -> Optional[tuple[str, str]]:
    """(name, uid) of the eval server pod to own case Jobs, or None.

    Only returned when the pod lives in the namespace the Jobs are created
    in. That check is not cosmetic: ownerReferences cannot cross namespaces,
    and the garbage collector treats a dependent whose owner it cannot find in
    its own namespace as orphaned and deletes it immediately -- every Job would
    vanish the moment it was created.
    """
    environ = os.environ if environ is None else environ
    name = environ.get("EVALBENCH_POD_NAME")
    uid = environ.get("EVALBENCH_POD_UID")
    if not name or not uid:
        return None
    namespace = _pod_namespace(environ, namespace_file)
    if namespace != job_namespace:
        logging.info(
            "k8s: eval server pod is in namespace %r but case Jobs go to %r; "
            "Jobs will not be owned by the pod and rely on their TTL alone.",
            namespace, job_namespace)
        return None
    return name, uid


# -- retries ---------------------------------------------------------------


def _api_status(exc: BaseException) -> Optional[int]:
    from kubernetes.client.rest import ApiException

    return exc.status if isinstance(exc, ApiException) else None


def is_retryable(exc: BaseException) -> bool:
    """Whether an API call that raised `exc` is worth repeating."""
    status = _api_status(exc)
    if status is not None:
        return status in _RETRYABLE_STATUSES
    import urllib3

    return isinstance(
        exc, (urllib3.exceptions.HTTPError, ConnectionError, TimeoutError))


def _retry_after_seconds(exc: BaseException) -> Optional[float]:
    headers = getattr(exc, "headers", None) or {}
    try:
        value = headers.get("Retry-After")
    except AttributeError:
        return None
    try:
        return float(value) if value is not None else None
    except (TypeError, ValueError):
        return None


class _AlreadyExists:
    """Sentinel: a retried create found its object already there."""


ALREADY_EXISTS = _AlreadyExists()


# -- status cache ----------------------------------------------------------


class _JobStatusCache:
    """One LIST per poll interval, shared by every waiter in the process.

    There is no background thread. A waiter that finds the snapshot older
    than the interval refreshes it; waiters that arrive mid-refresh block
    until it lands rather than issuing their own LIST.

    `lookup` returns:
      - the Job's status, when the latest snapshot has it;
      - "running", when the Job was submitted after that snapshot's LIST
        started, so its absence means nothing yet;
      - None, when the caller should confirm with a direct GET: the Job was
        submitted before the LIST started but is missing from it (reaped or
        deleted), or the last refresh failed.
    """

    def __init__(
        self,
        list_statuses: Callable[[], dict[str, str]],
        interval_seconds: float,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._list_statuses = list_statuses
        self._interval = max(0.0, float(interval_seconds))
        self._clock = clock
        self._cond = threading.Condition()
        self._statuses: dict[str, str] = {}
        self._snapshot_started: Optional[float] = None
        self._healthy = False
        self._refreshing = False
        self._generation = 0

    def _fresh(self) -> bool:
        return (
            self._healthy
            and self._snapshot_started is not None
            and self._clock() - self._snapshot_started < self._interval
        )

    def refresh_if_stale(self) -> None:
        with self._cond:
            if self._fresh():
                return
            if self._refreshing:
                generation = self._generation
                while self._refreshing and self._generation == generation:
                    self._cond.wait(timeout=60)
                return
            self._refreshing = True

        started = self._clock()
        statuses = None
        try:
            statuses = self._list_statuses()
        except Exception as e:  # pylint: disable=broad-except
            logging.warning(
                "k8s: shared job status LIST failed; falling back to per-job "
                "reads until it recovers: %s", e)
        finally:
            with self._cond:
                self._refreshing = False
                self._generation += 1
                if statuses is not None:
                    self._statuses = statuses
                    self._snapshot_started = started
                    self._healthy = True
                else:
                    self._healthy = False
                self._cond.notify_all()

    def lookup(self, name: str, submitted_at: float) -> Optional[str]:
        self.refresh_if_stale()
        with self._cond:
            if not self._healthy or self._snapshot_started is None:
                return None
            status = self._statuses.get(name)
            if status is not None:
                return status
            if submitted_at >= self._snapshot_started:
                return "running"
            return None


@dataclass
class _HandleState:
    """Backend-private bookkeeping carried on a `CaseHandle`."""

    # `clock()` reading taken once the Job create returned.
    submitted_at: float


def job_status_of(job) -> str:
    """Maps a V1Job to 'succeeded', 'failed', or 'running'."""
    status = job.status
    if status is None:
        return "running"
    if status.succeeded:
        return "succeeded"
    if status.failed:
        return "failed"
    for condition in status.conditions or []:
        if condition.type == "Failed" and condition.status == "True":
            return "failed"
    return "running"


def _tail(text: str, max_chars: int = _MAX_RESULT_LOG_CHARS) -> str:
    if len(text) <= max_chars:
        return text
    dropped = len(text) - max_chars
    return f"<{dropped} earlier characters truncated>\n" + text[-max_chars:]


class KubernetesJobBackend(ContainerBackend):
    """Submits eval cases as Jobs and collects their results from pod logs."""

    def __init__(
        self,
        config: ContainerizationConfig,
        *,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        super().__init__(config)
        from kubernetes import client, config as k8s_config
        from kubernetes.config.config_exception import ConfigException

        try:
            k8s_config.load_incluster_config()
            logging.info("k8s: using in-cluster credentials")
        except ConfigException:
            k8s_config.load_kube_config()
            logging.info("k8s: using local kubeconfig")

        self._k8s = client
        self._batch = client.BatchV1Api()
        self._core = client.CoreV1Api()
        self._counter = 0
        self._counter_lock = threading.Lock()
        self._sleep = sleep
        self._clock = clock

        self.dispatcher = dispatcher_id()
        self._owner = owner_pod(config.namespace)
        self._adopt_warned = False
        self._status_cache = _JobStatusCache(
            self._list_job_statuses, config.poll_interval_seconds, clock=clock)
        logging.info(
            "k8s: dispatcher %s, jobs %s", self.dispatcher,
            f"owned by pod {self._owner[0]}" if self._owner else "unowned")

    # -- retries ---------------------------------------------------------

    def _call(self, what: str, fn: Callable, *, create: bool = False, **kwargs):
        """Calls `fn(**kwargs)`, retrying transient failures.

        With `create=True`, a 409 on a *retry* means an earlier attempt that
        looked like it failed (a dropped response, a 504 from a proxy) did in
        fact create the object; that returns `ALREADY_EXISTS` rather than
        raising. A 409 on the first attempt is a genuine name collision and
        still raises.
        """
        attempt = 0
        while True:
            try:
                return fn(**kwargs)
            except Exception as e:  # pylint: disable=broad-except
                if create and attempt > 0 and _api_status(e) == 409:
                    return ALREADY_EXISTS
                attempt += 1
                if attempt >= _MAX_ATTEMPTS or not is_retryable(e):
                    raise
                delay = min(
                    _BACKOFF_MAX_SECONDS,
                    _BACKOFF_BASE_SECONDS * (2 ** (attempt - 1)),
                ) * random.uniform(0.5, 1.0)
                retry_after = _retry_after_seconds(e)
                if retry_after is not None:
                    delay = min(_BACKOFF_MAX_SECONDS, max(delay, retry_after))
                logging.warning(
                    "k8s: %s failed (%s); retry %d/%d in %.1fs",
                    what, getattr(e, "reason", None) or e, attempt,
                    _MAX_ATTEMPTS - 1, delay)
                self._sleep(delay)

    # -- submission ------------------------------------------------------

    def submit(
        self,
        spec: EvalCaseSpec,
        pool: WorkerPool,
        deadline_seconds: int,
    ) -> CaseHandle:
        name = self._object_name(spec)
        self._create_configmap(name, spec)
        try:
            job_uid = self._create_job(name, spec, pool, deadline_seconds)
        except Exception:
            # Never strand a ConfigMap whose Job failed to create: nothing else
            # will ever garbage-collect it.
            self._delete_configmap(name)
            raise
        submitted_at = self._clock()
        self._adopt_configmap(name, job_uid)
        logging.info(
            "k8s: submitted case %s as job %s on worker pool %s "
            "(deadline %ss)", spec.case_id, name, pool.name, deadline_seconds)
        return CaseHandle(
            case_id=spec.case_id,
            pool=pool.name,
            name=name,
            backend_state=_HandleState(submitted_at=submitted_at),
        )

    def _object_name(self, spec: EvalCaseSpec) -> str:
        with self._counter_lock:
            self._counter += 1
            index = self._counter
        job_slug = sanitize_case_id(spec.job_id, max_len=8)
        suffix = f"-{job_slug}-{index}"
        budget = _MAX_NAME_LEN - len(_NAME_PREFIX) - 1 - len(suffix)
        case_slug = sanitize_case_id(spec.case_id, max_len=max(1, budget))
        return f"{_NAME_PREFIX}-{case_slug}{suffix}"

    def _labels(self, spec: EvalCaseSpec, pool: Optional[str] = None) -> dict:
        labels = {
            "app": APP_LABEL,
            DISPATCHER_LABEL: self.dispatcher,
            "evalbench.io/job-id": sanitize_case_id(spec.job_id, max_len=63),
            "evalbench.io/case-id": sanitize_case_id(spec.case_id, max_len=63),
        }
        if pool:
            labels["evalbench.io/worker-pool"] = sanitize_case_id(pool, max_len=63)
        return labels

    def _create_configmap(self, name: str, spec: EvalCaseSpec) -> None:
        size = spec.size_bytes
        if size > _CONFIGMAP_MAX_BYTES:
            raise ValueError(
                f"Eval case {spec.case_id} serializes to {size} bytes, over the "
                f"{_CONFIGMAP_MAX_BYTES}-byte ConfigMap limit. Trim the "
                f"scenario, or bake its large inputs into the case image."
            )
        if size > _CONFIGMAP_WARN_BYTES:
            logging.warning(
                "k8s: case %s spec is %d bytes, close to the 1 MiB ConfigMap "
                "limit", spec.case_id, size)

        body = self._k8s.V1ConfigMap(
            metadata=self._k8s.V1ObjectMeta(
                name=name,
                namespace=self.config.namespace,
                labels=self._labels(spec),
            ),
            data=spec.to_files(),
        )
        self._call(
            f"create configmap {name}",
            self._core.create_namespaced_config_map,
            create=True,
            namespace=self.config.namespace,
            body=body,
        )

    def _owner_references(self) -> Optional[list]:
        if not self._owner:
            return None
        name, uid = self._owner
        return [
            self._k8s.V1OwnerReference(
                api_version="v1",
                kind="Pod",
                name=name,
                uid=uid,
                controller=False,
                # Blocking would need `update` on pods/finalizers; the Job has
                # nothing to protect the pod from.
                block_owner_deletion=False,
            )
        ]

    def _create_job(
        self,
        name: str,
        spec: EvalCaseSpec,
        pool: WorkerPool,
        deadline_seconds: int,
    ) -> Optional[str]:
        """Creates the Job and returns its UID when the API reports one."""
        cfg = self.config
        client = self._k8s

        volume_mounts = [
            client.V1VolumeMount(name="case-spec", mount_path=CASE_DIR, read_only=True),
            client.V1VolumeMount(name="tmp", mount_path="/tmp"),
        ]
        volumes = [
            client.V1Volume(
                name="case-spec",
                config_map=client.V1ConfigMapVolumeSource(name=name),
            ),
            client.V1Volume(name="tmp", empty_dir=client.V1EmptyDirVolumeSource()),
        ]
        for secret in cfg.secrets:
            vol_name = f"secret-{sanitize_case_id(secret.name, max_len=40)}"
            volume_mounts.append(
                client.V1VolumeMount(
                    name=vol_name,
                    mount_path=secret.mount_path,
                    read_only=secret.read_only,
                )
            )
            volumes.append(
                client.V1Volume(
                    name=vol_name,
                    secret=client.V1SecretVolumeSource(
                        secret_name=secret.name,
                        default_mode=secret.default_mode,
                        optional=secret.optional,
                    ),
                )
            )

        env_vars = {
            "EVALBENCH_CASE_DIR": CASE_DIR,
            "EVALBENCH_JOB_ID": spec.job_id,
            "EVALBENCH_CASE_ID": spec.case_id,
            "EVALBENCH_WORKER_POOL": pool.name,
            # The image's PYTHONPATH resolves `.` against the working
            # directory; keep it explicit so the generated protos import.
            "PROTOCOL_BUFFERS_PYTHON_IMPLEMENTATION": "python",
        }
        # A case pod inherits none of the eval server Deployment's environment,
        # so the vars the simulated user and LLM judges resolve their Vertex
        # project/region from have to be carried across explicitly.
        env_vars.update(cfg.resolved_env())
        env = [
            client.V1EnvVar(name=name, value=value)
            for name, value in env_vars.items()
        ]

        resources = cfg.pool_resources(pool) or {}
        resource_requirements = client.V1ResourceRequirements(
            requests=resources.get("requests") or resources or None,
            limits=resources.get("limits") or resources or None,
        )

        container = client.V1Container(
            name="eval-case",
            image=cfg.image,
            image_pull_policy=cfg.image_pull_policy,
            command=list(cfg.command),
            args=[f"--case_dir={CASE_DIR}"],
            working_dir="/evalbench",
            env=env,
            resources=resource_requirements,
            volume_mounts=volume_mounts,
        )

        node_selector = {cfg.pool_selector_label: pool.name}
        node_selector.update(pool.node_selector)

        pod_spec = client.V1PodSpec(
            restart_policy="Never",
            service_account_name=cfg.service_account,
            node_selector=node_selector,
            tolerations=[
                client.V1Toleration(
                    key=cfg.taint_key,
                    operator="Equal",
                    value=cfg.taint_value,
                    effect="NoSchedule",
                )
            ],
            containers=[container],
            volumes=volumes,
            # Claude Code's sandbox chowns its fake home for the non-root
            # `claudeuser`, which needs root in the container -- the same
            # security context the eval server itself runs under.
            security_context=client.V1PodSecurityContext(
                run_as_user=0, run_as_group=0),
        )

        job = client.V1Job(
            metadata=client.V1ObjectMeta(
                name=name,
                namespace=cfg.namespace,
                labels=self._labels(spec, pool.name),
                # A dispatcher that dies mid-run takes its Jobs with it
                # instead of leaving them to burn their full deadline.
                owner_references=self._owner_references(),
            ),
            spec=client.V1JobSpec(
                # One shot: a retried eval case would double-charge tokens and
                # report a run the orchestrator never asked for.
                backoff_limit=0,
                active_deadline_seconds=deadline_seconds,
                ttl_seconds_after_finished=cfg.ttl_seconds_after_finished,
                template=client.V1PodTemplateSpec(
                    metadata=client.V1ObjectMeta(
                        labels=self._labels(spec, pool.name)),
                    spec=pod_spec,
                ),
            ),
        )
        created = self._call(
            f"create job {name}",
            self._batch.create_namespaced_job,
            create=True,
            namespace=cfg.namespace,
            body=job,
        )
        if created is ALREADY_EXISTS:
            created = self._call(
                f"read job {name}",
                self._batch.read_namespaced_job,
                name=name,
                namespace=cfg.namespace,
            )
        uid = getattr(getattr(created, "metadata", None), "uid", None)
        return uid if isinstance(uid, str) and uid else None

    def _adopt_configmap(self, name: str, job_uid: Optional[str]) -> None:
        """Makes the Job own its ConfigMap, so GC removes both together.

        Without this a kept failed Job is reaped by its TTL but its ConfigMap
        stays forever. Best effort: explicit cleanup still deletes both, so a
        failure here (e.g. RBAC missing `patch` on configmaps) only costs the
        leak protection, not the case.
        """
        if not job_uid:
            return
        from kubernetes.client.rest import ApiException

        body = {
            "metadata": {
                "ownerReferences": [{
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "name": name,
                    "uid": job_uid,
                    "controller": False,
                    "blockOwnerDeletion": False,
                }]
            }
        }
        try:
            self._call(
                f"patch configmap {name}",
                self._core.patch_namespaced_config_map,
                name=name,
                namespace=self.config.namespace,
                body=body,
            )
        except ApiException as e:
            if not self._adopt_warned:
                self._adopt_warned = True
                hint = (
                    " -- grant `patch` on configmaps (worker_rbac.yaml)"
                    if e.status == 403 else "")
                logging.warning(
                    "k8s: could not make job %s own its ConfigMap (%s)%s; "
                    "ConfigMaps of kept failed jobs will outlive their TTL.",
                    name, e.reason, hint)

    # -- collection ------------------------------------------------------

    def wait(
        self, handle: CaseHandle, timeout_seconds: Optional[float] = None
    ) -> CaseResult:
        # `is not None`, not truthiness: a 0s timeout means "already expired",
        # not "wait forever".
        deadline = (
            self._clock() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        interval = self.config.poll_interval_seconds

        while True:
            status = self._job_status(handle)
            if status in ("succeeded", "failed"):
                return self._collect(handle, failed=(status == "failed"))

            if deadline is not None and self._clock() >= deadline:
                logs = self._pod_logs(handle.name, tail_lines=_LOG_TAIL_LINES)
                detail = self._scheduling_detail(handle.name)
                return CaseResult(
                    case_id=handle.case_id,
                    error=(
                        f"Case container did not finish within "
                        f"{timeout_seconds:.0f}s. {detail}"
                    ),
                    pool=handle.pool,
                    container_ref=handle.name,
                    logs=_tail(logs),
                )
            self._sleep(interval)

    def _job_status(self, handle: CaseHandle) -> str:
        """Returns 'succeeded', 'failed', or 'running'."""
        state = handle.backend_state
        submitted_at = (
            state.submitted_at if isinstance(state, _HandleState)
            else float("-inf"))
        status = self._status_cache.lookup(handle.name, submitted_at)
        if status is None:
            status = self._read_job_status(handle.name)
        return status

    def _list_job_statuses(self) -> dict[str, str]:
        """Statuses of every Job this dispatcher owns, in one paged LIST."""
        selector = f"app={APP_LABEL},{DISPATCHER_LABEL}={self.dispatcher}"
        statuses: dict[str, str] = {}
        token = None
        while True:
            kwargs = {
                "namespace": self.config.namespace,
                "label_selector": selector,
                "limit": _LIST_PAGE_SIZE,
            }
            if token:
                kwargs["_continue"] = token
            page = self._call(
                "list jobs", self._batch.list_namespaced_job, **kwargs)
            for job in page.items or []:
                statuses[job.metadata.name] = job_status_of(job)
            token = getattr(page.metadata, "_continue", None)
            if not isinstance(token, str) or not token:
                return statuses

    def _read_job_status(self, name: str) -> str:
        from kubernetes.client.rest import ApiException

        try:
            job = self._call(
                f"read job {name} status",
                self._batch.read_namespaced_job_status,
                name=name,
                namespace=self.config.namespace,
            )
        except ApiException as e:
            if e.status == 404:
                # TTL reaped the Job before we polled it. Treat it as failed;
                # the logs are gone with it, so there is nothing to collect.
                return "failed"
            raise
        return job_status_of(job)

    def _collect(self, handle: CaseHandle, failed: bool) -> CaseResult:
        logs = self._pod_logs(handle.name, tail_lines=_LOG_TAIL_LINES)
        payload = extract_result_payload(logs)
        if payload is None:
            # The payload line is last, but a crashing harness can print past
            # it; only then is the whole log worth pulling.
            logs = self._pod_logs(handle.name)
            payload = extract_result_payload(logs)

        if payload is None:
            detail = self._scheduling_detail(handle.name)
            return CaseResult(
                case_id=handle.case_id,
                error=(
                    "Case container produced no result payload "
                    f"({'job failed' if failed else 'job reported success'}). "
                    f"{detail}"
                ),
                pool=handle.pool,
                container_ref=handle.name,
                logs=_tail(logs),
            )

        result = CaseResult.from_payload(handle.case_id, payload)
        result.pool = handle.pool
        result.container_ref = handle.name
        result.logs = _tail(logs)
        return result

    def _pods_for_job(self, name: str) -> list:
        pods = self._call(
            f"list pods for job {name}",
            self._core.list_namespaced_pod,
            namespace=self.config.namespace,
            label_selector=f"job-name={name}",
        )
        return list(pods.items)

    def _read_pod_log(self, pod_name: str, tail_lines: Optional[int] = None) -> str:
        """Reads one pod's log as text.

        `_preload_content=False` is load-bearing, not an optimization. With the
        default the generated client hands the raw bytes to its `str`
        deserializer, which calls `str()` on them -- so the caller gets the
        *repr* of a bytes object: a literal ``b'...'`` wrapper with every
        newline escaped to a backslash-n. The sentinel markers survive that
        (they contain no escapes) but the JSON between them does not, and
        `json.loads` rejects a backslash where it expects whitespace. Every
        case would come back as "produced no result payload".
        """
        kwargs = {
            "name": pod_name,
            "namespace": self.config.namespace,
            "container": "eval-case",
            "_preload_content": False,
        }
        if tail_lines is not None:
            kwargs["tail_lines"] = tail_lines
        response = self._call(
            f"read log of pod {pod_name}",
            self._core.read_namespaced_pod_log,
            **kwargs,
        )
        return response.data.decode("utf-8", errors="replace")

    def _pod_logs(self, name: str, tail_lines: Optional[int] = None) -> str:
        from kubernetes.client.rest import ApiException

        chunks = []
        try:
            pods = self._pods_for_job(name)
        except ApiException as e:
            return f"<failed to list pods for job {name}: {e.reason}>"

        for pod in pods:
            try:
                chunks.append(self._read_pod_log(pod.metadata.name, tail_lines))
            except ApiException as e:
                # A pod that never started (ImagePullBackOff, Unschedulable)
                # has no logs; its status is reported separately.
                chunks.append(
                    f"<no logs for pod {pod.metadata.name}: {e.reason}>")
        return "\n".join(chunks)

    def _scheduling_detail(self, name: str) -> str:
        """A short human-readable reason for why a case has no result."""
        from kubernetes.client.rest import ApiException

        try:
            pods = self._pods_for_job(name)
        except ApiException as e:
            return f"Could not inspect pods for job {name}: {e.reason}"
        if not pods:
            return (
                f"Job {name} has no pods -- it was most likely never scheduled "
                f"onto its worker pool."
            )

        details = []
        for pod in pods:
            status = pod.status
            parts = [f"pod {pod.metadata.name} phase={status.phase}"]
            for condition in status.conditions or []:
                if condition.status != "True" and condition.reason:
                    parts.append(f"{condition.type}={condition.reason}")
            for cs in (status.container_statuses or []):
                waiting = cs.state.waiting if cs.state else None
                terminated = cs.state.terminated if cs.state else None
                if waiting and waiting.reason:
                    parts.append(f"waiting={waiting.reason}")
                if terminated:
                    parts.append(
                        f"exit={terminated.exit_code} reason={terminated.reason}")
            details.append(", ".join(parts))
        return "; ".join(details)

    # -- teardown --------------------------------------------------------

    def cleanup(self, handle: CaseHandle, succeeded: bool) -> None:
        if not succeeded and self.config.keep_failed_jobs:
            logging.info(
                "k8s: keeping failed job %s (and its ConfigMap) for inspection; "
                "`kubectl -n %s describe job %s`",
                handle.name, self.config.namespace, handle.name)
            return
        self._delete_job(handle.name)
        self._delete_configmap(handle.name)

    def _delete_job(self, name: str) -> None:
        from kubernetes.client.rest import ApiException

        try:
            self._call(
                f"delete job {name}",
                self._batch.delete_namespaced_job,
                name=name,
                namespace=self.config.namespace,
                body=self._k8s.V1DeleteOptions(propagation_policy="Background"),
            )
        except ApiException as e:
            if e.status != 404:
                logging.warning("k8s: failed to delete job %s: %s", name, e.reason)

    def _delete_configmap(self, name: str) -> None:
        from kubernetes.client.rest import ApiException

        try:
            self._call(
                f"delete configmap {name}",
                self._core.delete_namespaced_config_map,
                name=name,
                namespace=self.config.namespace,
            )
        except ApiException as e:
            if e.status != 404:
                logging.warning(
                    "k8s: failed to delete configmap %s: %s", name, e.reason)
