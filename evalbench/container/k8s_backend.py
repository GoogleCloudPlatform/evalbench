"""Runs each eval case as a Kubernetes Job pinned to a worker (node) pool.

The orchestrator pod stays where it is; every eval case becomes a short-lived
Job whose pod is placed on one of the dedicated worker pools created by
`evalbench_service/k8s/worker_pools.sh`. The case spec travels as a ConfigMap
mounted into the pod, and the result travels back through the pod's logs as a
sentinel-delimited JSON blob -- the cluster's PVC is ReadWriteOnce, so a shared
filesystem is not available across nodes.

Object lifetimes are left to the Kubernetes garbage collector wherever
possible, so nothing leaks when this process dies mid-run:

    eval server Pod  --owns-->  case Job  --owns-->  case ConfigMap
                                          --owns-->  case Pod
"""

from typing import Optional
import logging
import os
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

_APP_LABEL = "evalbench-eval-case"
_DISPATCHER_LABEL = "evalbench.io/dispatcher"
_CONTAINER_NAME = "eval-case"

# Container waiting reasons that will not fix themselves. Without these a case
# with a typo'd image or a missing Secret key sits Pending until its
# `activeDeadlineSeconds` -- by default the case timeout plus ten minutes.
_FAIL_NOW_REASONS = frozenset({
    "InvalidImageName",
    "ErrImageNeverPull",
    "CreateContainerConfigError",
})
# Reasons that are usually transient but become fatal if they persist past
# `image_pull_grace_seconds`. ContainerCreating covers a Secret volume that
# never mounts (FailedMount surfaces only as an event, not a waiting reason).
_FAIL_AFTER_GRACE_REASONS = frozenset({
    "ErrImagePull",
    "ImagePullBackOff",
    "ContainerCreating",
})

# Concurrent sessions share one backend, and each case thread makes a handful
# of calls around submit and collect. The client's default pool is cpu_count*5
# connections; at hundreds of cases that churns connections and logs
# "Connection pool is full, discarding connection".
_CONNECTION_POOL_SIZE = 64


class _ClusterView:
    """One LIST of case Jobs and one of case Pods per poll, shared by every case.

    Polling each Job individually costs one API call per in-flight case per
    poll interval -- at 300 concurrent cases and a 5 s interval, 60 QPS against
    the control plane, rising linearly with the fan-out. Here the first waiter
    whose snapshot has gone stale refreshes it for everyone ("single flight"),
    so the cost is two LISTs per interval regardless of how many cases are
    running.
    """

    def __init__(self, batch, core, namespace: str, label_selector: str):
        self._batch = batch
        self._core = core
        self._namespace = namespace
        self._selector = label_selector
        self._refresh_lock = threading.Lock()
        self._changed = threading.Condition()
        self._jobs: dict = {}
        self._pods: dict = {}
        # Monotonic start time of the snapshot currently held. A Job created
        # before this instant must appear in it; one that does not is gone.
        self._snapshot_started: Optional[float] = None
        # Start of the last LIST attempt, successful or not; paces retries.
        self._last_attempt: Optional[float] = None
        # job name -> monotonic time a slow-start waiting reason was first seen.
        self._waiting_since: dict[str, float] = {}

    def refresh_if_stale(self, max_age: float) -> None:
        with self._refresh_lock:
            last = self._last_attempt
            if last is not None and time.monotonic() - last < max_age:
                return
            from kubernetes.client.rest import ApiException

            started = time.monotonic()
            self._last_attempt = started
            try:
                jobs = self._batch.list_namespaced_job(
                    namespace=self._namespace, label_selector=self._selector)
                pods = self._core.list_namespaced_pod(
                    namespace=self._namespace, label_selector=self._selector)
            except ApiException as e:
                # Keep the stale snapshot and back off for one interval rather
                # than hammering a struggling API server; case deadlines still
                # bound how long a case can wait.
                logging.warning(
                    "k8s: listing case jobs failed (%s); retrying next poll",
                    e.reason)
                return

            pods_by_job: dict = {}
            for pod in pods.items:
                job_name = (pod.metadata.labels or {}).get("job-name")
                if job_name:
                    # backoffLimit is 0, so there is at most one pod per Job.
                    pods_by_job[job_name] = pod
            with self._changed:
                self._jobs = {job.metadata.name: job for job in jobs.items}
                self._pods = pods_by_job
                self._snapshot_started = started
                self._changed.notify_all()

    def wait_for_refresh(self, timeout: float) -> None:
        with self._changed:
            self._changed.wait(timeout=max(timeout, 0.0))

    def observe(self, handle: CaseHandle, pull_grace: float) -> tuple[str, Optional[object], Optional[str]]:
        """Returns (state, pod, fail_reason) for a case from the snapshot.

        state is one of "unknown" (no snapshot taken since submit), "running",
        "succeeded", "failed", or "missing".
        """
        with self._changed:
            started = self._snapshot_started
            job = self._jobs.get(handle.name)
            pod = self._pods.get(handle.name)

        if started is None or started < handle.submitted_at:
            return "unknown", pod, None
        if job is None:
            return "missing", pod, None

        state = _job_state(job)
        if state != "running":
            self._waiting_since.pop(handle.name, None)
            return state, pod, None
        return "running", pod, self._fail_fast_reason(handle.name, pod, pull_grace)

    def _fail_fast_reason(self, name: str, pod, pull_grace: float) -> Optional[str]:
        if pod is None or pod.status is None:
            return None
        for cs in pod.status.container_statuses or []:
            waiting = cs.state.waiting if cs.state else None
            reason = getattr(waiting, "reason", None)
            if reason in _FAIL_NOW_REASONS:
                return f"{reason}: {getattr(waiting, 'message', '') or ''}".strip()
            if reason in _FAIL_AFTER_GRACE_REASONS:
                first_seen = self._waiting_since.setdefault(name, time.monotonic())
                if time.monotonic() - first_seen >= pull_grace:
                    return (
                        f"{reason} for over {pull_grace:.0f}s: "
                        f"{getattr(waiting, 'message', '') or ''}"
                    ).strip()
                return None
        self._waiting_since.pop(name, None)
        return None

    def forget(self, name: str) -> None:
        self._waiting_since.pop(name, None)


def _job_state(job) -> str:
    status = job.status
    if status is None:
        return "running"
    if status.succeeded:
        return "succeeded"
    if status.failed:
        return "failed"
    for condition in status.conditions or []:
        if condition.status != "True":
            continue
        if condition.type == "Failed":
            return "failed"
        if condition.type == "Complete":
            return "succeeded"
    return "running"


class KubernetesJobBackend(ContainerBackend):
    """Submits eval cases as Jobs and collects their results from pod logs."""

    def __init__(self, config: ContainerizationConfig) -> None:
        super().__init__(config)
        from kubernetes import client, config as k8s_config
        from kubernetes.config.config_exception import ConfigException

        try:
            k8s_config.load_incluster_config()
            logging.info("k8s: using in-cluster credentials")
        except ConfigException:
            k8s_config.load_kube_config()
            logging.info("k8s: using local kubeconfig")

        configuration = client.Configuration.get_default_copy()
        configuration.connection_pool_maxsize = _CONNECTION_POOL_SIZE
        self._api_client = client.ApiClient(configuration)

        self._k8s = client
        self._batch = client.BatchV1Api(self._api_client)
        self._core = client.CoreV1Api(self._api_client)
        self._counter = 0
        self._counter_lock = threading.Lock()
        self._closed = False

        # The server pod, when known, owns every case Job (see module doc).
        # POD_NAME / POD_UID come from the Deployment's downward API.
        self._server_pod_name = os.environ.get("POD_NAME") or None
        self._server_pod_uid = os.environ.get("POD_UID") or None
        self._dispatcher_id = sanitize_case_id(
            self._server_pod_name or socket.gethostname(), max_len=63)

        self._selector = (
            f"app={_APP_LABEL},{_DISPATCHER_LABEL}={self._dispatcher_id}")

        self.image = self._resolve_image()
        self._view = _ClusterView(
            self._batch, self._core, config.namespace, self._selector)
        self._preflight()
        self._sweep_orphans()

    # -- startup ---------------------------------------------------------

    def _resolve_image(self) -> str:
        """The image case pods run, pinned to a digest when possible.

        By default that is the eval server's own image. Pinning it matters
        twice over: every case in a run gets byte-identical code even if
        `:latest` is pushed mid-run, and the case runner always understands
        the spec format of the orchestrator that wrote the spec.
        """
        if not self.config.uses_self_image:
            if "@sha256:" not in self.config.image:
                logging.warning(
                    "k8s: case image %s is a mutable tag; cases in one run can "
                    "land on different builds if it is pushed mid-run. Leave "
                    "`containerization.image` unset to pin to the eval "
                    "server's own digest.", self.config.image)
            return self.config.image

        from kubernetes.client.rest import ApiException

        pod_name = self._server_pod_name or socket.gethostname()
        try:
            pod = self._core.read_namespaced_pod(
                name=pod_name, namespace=self.config.namespace)
        except ApiException as e:
            raise ValueError(
                f"containerization.image is unset, which means 'run cases on "
                f"the eval server's own image', but this process could not "
                f"read its own pod {self.config.namespace}/{pod_name} "
                f"({e.reason}). Outside the cluster, set "
                f"`containerization.image` explicitly.") from e

        wanted = os.environ.get("EVALBENCH_SERVER_CONTAINER", "evalbench-eval")
        statuses = list(pod.status.container_statuses or [])
        chosen = next((cs for cs in statuses if cs.name == wanted), None)
        if chosen is None and statuses:
            chosen = statuses[0]
        if chosen is None:
            raise ValueError(
                f"Pod {pod_name} reports no container statuses; cannot "
                f"resolve the eval server image. Set `containerization.image`.")

        image_id = (chosen.image_id or "").split("://", 1)[-1]
        if "@sha256:" in image_id:
            logging.info("k8s: case pods will run the server image %s", image_id)
            return image_id
        logging.warning(
            "k8s: could not resolve a digest for container %s (imageID=%r); "
            "falling back to its tag %s", chosen.name, chosen.image_id,
            chosen.image)
        return chosen.image

    def _preflight(self) -> None:
        """Fails the run up front on setup errors that would fail every case."""
        from kubernetes.client.rest import ApiException

        try:
            self._core.read_namespaced_service_account(
                name=self.config.service_account,
                namespace=self.config.namespace)
        except ApiException as e:
            if e.status == 404:
                raise ValueError(
                    f"Case service account {self.config.namespace}/"
                    f"{self.config.service_account} does not exist; every case "
                    f"Job would fail to create its pod. Create it with "
                    f"`evalbench_service/k8s/worker_pools.sh create`.") from e
            # 403: older RBAC without serviceaccounts/get. Not fatal.
            logging.warning(
                "k8s: could not verify case service account %s (%s)",
                self.config.service_account, e.reason)

    def _sweep_orphans(self) -> None:
        """Deletes unfinished case Jobs a previous incarnation left behind.

        A fresh backend has submitted nothing, so any still-running Job with
        this dispatcher's label belongs to an earlier process in the same pod
        (supervisord restarts the server in place; the pod -- and so the
        ownerReference GC -- survives that). Nobody will collect those
        results; stop paying for them.
        """
        from kubernetes.client.rest import ApiException

        try:
            jobs = self._batch.list_namespaced_job(
                namespace=self.config.namespace,
                label_selector=self._selector)
        except ApiException as e:
            logging.warning("k8s: orphan sweep skipped: %s", e.reason)
            return
        orphans = [j.metadata.name for j in jobs.items if _job_state(j) == "running"]
        if not orphans:
            return
        logging.warning(
            "k8s: deleting %d orphaned case job(s) from a previous eval "
            "server process: %s", len(orphans), orphans)
        for name in orphans:
            self._delete_job(name)

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
            job = self._create_job(name, spec, pool, deadline_seconds)
        except Exception:
            # Never strand a ConfigMap whose Job failed to create: nothing else
            # will ever garbage-collect it.
            self._delete_configmap(name)
            raise
        self._adopt_configmap(name, job)
        logging.info(
            "k8s: submitted case %s as job %s on worker pool %s "
            "(deadline %ss)", spec.case_id, name, pool.name, deadline_seconds)
        return CaseHandle(case_id=spec.case_id, pool=pool.name, name=name)

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
            "app": _APP_LABEL,
            _DISPATCHER_LABEL: self._dispatcher_id,
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
        self._core.create_namespaced_config_map(
            namespace=self.config.namespace, body=body)

    def _adopt_configmap(self, name: str, job) -> None:
        """Makes the Job own its ConfigMap, so the TTL controller reaps both.

        Without this the Job's `ttlSecondsAfterFinished` deletes the Job but
        not the ConfigMap, and every kept-for-inspection failure leaks one.
        The ConfigMap has to exist before the Job (the pod mounts it), hence
        the patch after the fact rather than setting it at create time.
        """
        uid = getattr(getattr(job, "metadata", None), "uid", None)
        if not uid:
            return
        from kubernetes.client.rest import ApiException

        try:
            self._core.patch_namespaced_config_map(
                name=name,
                namespace=self.config.namespace,
                body={"metadata": {"ownerReferences": [{
                    "apiVersion": "batch/v1",
                    "kind": "Job",
                    "name": name,
                    "uid": uid,
                    "blockOwnerDeletion": False,
                }]}},
            )
        except ApiException as e:
            logging.warning(
                "k8s: could not attach configmap %s to its job (%s); it will "
                "only be deleted by explicit cleanup", name, e.reason)

    def _owner_references(self) -> Optional[list]:
        if not (self.config.bind_to_server_pod
                and self._server_pod_name and self._server_pod_uid):
            return None
        return [self._k8s.V1OwnerReference(
            api_version="v1",
            kind="Pod",
            name=self._server_pod_name,
            uid=self._server_pod_uid,
            # Needs no extra RBAC, and the server pod's deletion should not
            # wait on its cases anyway.
            block_owner_deletion=False,
            controller=False,
        )]

    def _create_job(
        self,
        name: str,
        spec: EvalCaseSpec,
        pool: WorkerPool,
        deadline_seconds: int,
    ):
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
            requests=resources.get("requests") or None,
            limits=resources.get("limits") or None,
        )

        container = client.V1Container(
            name=_CONTAINER_NAME,
            image=self.image,
            image_pull_policy=cfg.pull_policy_for(self.image),
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
            automount_service_account_token=cfg.automount_service_account_token,
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
        return self._batch.create_namespaced_job(namespace=cfg.namespace, body=job)

    # -- collection ------------------------------------------------------

    def wait(
        self,
        handle: CaseHandle,
        timeout_seconds: Optional[float] = None,
        cancel_event: Optional[threading.Event] = None,
    ) -> CaseResult:
        # `is not None`, not truthiness: a 0s timeout means "already expired",
        # not "wait forever".
        deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        interval = self.config.poll_interval_seconds

        try:
            while True:
                if cancel_event is not None and cancel_event.is_set():
                    handle.abandoned = True
                    return self._error_result(
                        handle, "Cancelled: the eval RPC that started this "
                        "case was cancelled before it finished.")

                self._view.refresh_if_stale(interval)
                state, pod, fail_reason = self._view.observe(
                    handle, self.config.image_pull_grace_seconds)

                if state in ("succeeded", "failed"):
                    return self._collect(handle, pod, failed=(state == "failed"))
                if state == "missing":
                    return self._error_result(
                        handle,
                        f"Job {handle.name} disappeared before reporting a "
                        f"result (deleted externally, or reaped by its TTL "
                        f"before it was collected).")
                if fail_reason:
                    handle.abandoned = True
                    return self._error_result(
                        handle,
                        f"Case pod cannot start: {fail_reason}. "
                        f"{self._scheduling_detail(handle.name, pod)}",
                        logs=self._pod_logs(handle.name, pod))

                if deadline is not None and time.monotonic() >= deadline:
                    handle.abandoned = True
                    result = self._error_result(
                        handle,
                        f"Case container did not finish within "
                        f"{timeout_seconds:.0f}s. "
                        f"{self._scheduling_detail(handle.name, pod)}",
                        logs=self._pod_logs(handle.name, pod))
                    result.timed_out = True
                    return result

                self._view.wait_for_refresh(interval)
        finally:
            self._view.forget(handle.name)

    def _error_result(self, handle: CaseHandle, error: str, logs: str = "") -> CaseResult:
        return CaseResult(
            case_id=handle.case_id,
            error=error,
            pool=handle.pool,
            container_ref=handle.name,
            logs=logs,
        )

    def _collect(self, handle: CaseHandle, pod, failed: bool) -> CaseResult:
        logs = self._pod_logs(handle.name, pod)
        payload = extract_result_payload(logs)

        if payload is None:
            detail = self._scheduling_detail(handle.name, pod)
            result = self._error_result(
                handle,
                "Case container produced no result payload "
                f"({'job failed' if failed else 'job reported success'}). "
                f"{detail}",
                logs=logs,
            )
            # A Job killed by activeDeadlineSeconds is a timeout, and should
            # be reported as one rather than as a crash.
            result.timed_out = "DeadlineExceeded" in detail
            return result

        result = CaseResult.from_payload(handle.case_id, payload)
        result.pool = handle.pool
        result.container_ref = handle.name
        result.logs = logs
        return result

    def _pods_for_job(self, name: str) -> list:
        pods = self._core.list_namespaced_pod(
            namespace=self.config.namespace, label_selector=f"job-name={name}")
        return list(pods.items)

    def _read_pod_log(self, pod_name: str) -> str:
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
        response = self._core.read_namespaced_pod_log(
            name=pod_name,
            namespace=self.config.namespace,
            container=_CONTAINER_NAME,
            _preload_content=False,
        )
        return response.data.decode("utf-8", errors="replace")

    def _pod_logs(self, name: str, pod=None) -> str:
        from kubernetes.client.rest import ApiException

        if pod is not None:
            pods = [pod]
        else:
            try:
                pods = self._pods_for_job(name)
            except ApiException as e:
                return f"<failed to list pods for job {name}: {e.reason}>"

        chunks = []
        for p in pods:
            try:
                chunks.append(self._read_pod_log(p.metadata.name))
            except ApiException as e:
                # A pod that never started (ImagePullBackOff, Unschedulable)
                # has no logs; its status is reported separately.
                chunks.append(f"<no logs for pod {p.metadata.name}: {e.reason}>")
        return "\n".join(chunks)

    def _scheduling_detail(self, name: str, pod=None) -> str:
        """A short human-readable reason for why a case has no result."""
        from kubernetes.client.rest import ApiException

        if pod is not None:
            pods = [pod]
        else:
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
        for p in pods:
            status = p.status
            parts = [f"pod {p.metadata.name} phase={status.phase}"]
            if getattr(status, "reason", None):
                parts.append(f"reason={status.reason}")
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
        if handle.abandoned:
            # Possibly still running: keeping it would only hold a node slot
            # and keep spending tokens until its deadline.
            logging.info("k8s: stopping abandoned case job %s", handle.name)
        elif not succeeded and self.config.keep_failed_jobs:
            logging.info(
                "k8s: keeping failed job %s (and its ConfigMap) for inspection "
                "for %ss; `kubectl -n %s describe job %s`",
                handle.name, self.config.ttl_seconds_after_finished,
                self.config.namespace, handle.name)
            return
        self._delete_job(handle.name)
        # Normally collected with the Job via its ownerReference; deleted
        # explicitly too in case the adoption patch failed.
        self._delete_configmap(handle.name)

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        try:
            self._api_client.close()
        except Exception:
            logging.debug("k8s: closing API client failed", exc_info=True)

    def _delete_job(self, name: str) -> None:
        from kubernetes.client.rest import ApiException

        try:
            self._batch.delete_namespaced_job(
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
            self._core.delete_namespaced_config_map(
                name=name, namespace=self.config.namespace)
        except ApiException as e:
            if e.status != 404:
                logging.warning(
                    "k8s: failed to delete configmap %s: %s", name, e.reason)
