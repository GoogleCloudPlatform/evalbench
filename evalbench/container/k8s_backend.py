"""Runs each eval case as a Kubernetes Job pinned to a worker (node) pool.

The orchestrator pod stays where it is; every eval case becomes a short-lived
Job whose pod is placed on one of the dedicated worker pools created by
`evalbench_service/k8s/worker_pools.sh`. The case spec travels as a ConfigMap
mounted into the pod, and the result travels back through the pod's logs as a
sentinel-delimited JSON blob -- the cluster's PVC is ReadWriteOnce, so a shared
filesystem is not available across nodes.
"""

from typing import Optional
import logging
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

        self._k8s = client
        self._batch = client.BatchV1Api()
        self._core = client.CoreV1Api()
        self._counter = 0
        self._counter_lock = threading.Lock()

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
            self._create_job(name, spec, pool, deadline_seconds)
        except Exception:
            # Never strand a ConfigMap whose Job failed to create: nothing else
            # will ever garbage-collect it.
            self._delete_configmap(name)
            raise
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
            "app": "evalbench-eval-case",
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

    def _create_job(
        self,
        name: str,
        spec: EvalCaseSpec,
        pool: WorkerPool,
        deadline_seconds: int,
    ) -> None:
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
        self._batch.create_namespaced_job(namespace=cfg.namespace, body=job)

    # -- collection ------------------------------------------------------

    def wait(
        self, handle: CaseHandle, timeout_seconds: Optional[float] = None
    ) -> CaseResult:
        # `is not None`, not truthiness: a 0s timeout means "already expired",
        # not "wait forever".
        deadline = (
            time.monotonic() + timeout_seconds
            if timeout_seconds is not None
            else None
        )
        interval = self.config.poll_interval_seconds

        while True:
            status = self._job_status(handle.name)
            if status in ("succeeded", "failed"):
                return self._collect(handle, failed=(status == "failed"))

            if deadline is not None and time.monotonic() >= deadline:
                logs = self._pod_logs(handle.name)
                detail = self._scheduling_detail(handle.name)
                return CaseResult(
                    case_id=handle.case_id,
                    error=(
                        f"Case container did not finish within "
                        f"{timeout_seconds:.0f}s. {detail}"
                    ),
                    pool=handle.pool,
                    container_ref=handle.name,
                    logs=logs,
                )
            time.sleep(interval)

    def _job_status(self, name: str) -> str:
        """Returns 'succeeded', 'failed', or 'running'."""
        from kubernetes.client.rest import ApiException

        try:
            job = self._batch.read_namespaced_job_status(
                name=name, namespace=self.config.namespace)
        except ApiException as e:
            if e.status == 404:
                # TTL reaped the Job before we polled it. Treat it as failed;
                # the logs are gone with it, so there is nothing to collect.
                return "failed"
            raise
        status = job.status
        if status.succeeded:
            return "succeeded"
        if status.failed:
            return "failed"
        for condition in status.conditions or []:
            if condition.type == "Failed" and condition.status == "True":
                return "failed"
        return "running"

    def _collect(self, handle: CaseHandle, failed: bool) -> CaseResult:
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
                logs=logs,
            )

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
            container="eval-case",
            _preload_content=False,
        )
        return response.data.decode("utf-8", errors="replace")

    def _pod_logs(self, name: str) -> str:
        from kubernetes.client.rest import ApiException

        chunks = []
        try:
            pods = self._pods_for_job(name)
        except ApiException as e:
            return f"<failed to list pods for job {name}: {e.reason}>"

        for pod in pods:
            try:
                chunks.append(self._read_pod_log(pod.metadata.name))
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
