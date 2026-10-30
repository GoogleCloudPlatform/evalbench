"""Parses the `containerization:` block of a run config."""

from dataclasses import dataclass, field
from typing import Any, Optional
import copy
import logging
import os

from util.config import parse_timeout_seconds

# GKE labels every node with its node pool name under this key, so a plain
# `nodeSelector` is all that is needed to pin a Job to one worker pool.
DEFAULT_POOL_LABEL = "cloud.google.com/gke-nodepool"

# Worker pools created by `evalbench_service/k8s/worker_pools.sh` carry this
# taint so that only eval-case Jobs (which tolerate it) land on them and the
# eval server itself keeps scheduling on the default pool.
DEFAULT_TAINT_KEY = "evalbench.io/dedicated"
DEFAULT_TAINT_VALUE = "eval-worker"

# Disk a case is assumed to need: the image itself is ~900 MiB and Claude Code
# npm-installs into a fresh sandbox per case. A node offers roughly 44 GiB of
# allocatable ephemeral storage, so these values cap a node at ~5 concurrent
# cases rather than letting the scheduler pack it until the kubelet starts
# evicting. See `ContainerizationConfig.pool_resources`.
DEFAULT_EPHEMERAL_STORAGE_REQUEST = "8Gi"
DEFAULT_EPHEMERAL_STORAGE_LIMIT = "20Gi"

DEFAULT_NAMESPACE = "evalbench-namespace"
# Case pods run the agent under eval -- as root, executing whatever the model
# decides to. They get their own KSA, bound to the same GCP service account via
# Workload Identity but holding no Kubernetes RBAC, so an agent cannot reach
# the dispatcher's rights to create, read, or delete Jobs and ConfigMaps in the
# namespace. Created by `evalbench_service/k8s/worker_pools.sh`.
DEFAULT_SERVICE_ACCOUNT = "evalbench-case-ksa"

# `image:` value (and the meaning of leaving it unset) for "run cases on the
# exact image this eval server is running", resolved to a digest at startup.
SELF_IMAGE = "self"

# How long a case may sit in ErrImagePull / ImagePullBackOff before it is
# failed. Pull errors are often transient (registry blips, node cold start),
# so these get a grace period; config errors such as a missing Secret or a
# malformed image reference fail immediately.
DEFAULT_IMAGE_PULL_GRACE_SECONDS = 300.0

# Run the case runner the same way supervisord runs eval_server.py -- by path,
# so Python puts /evalbench/evalbench on sys.path and the flat intra-package
# imports (`from work import work`) resolve.
DEFAULT_CASE_COMMAND = ["python", "/evalbench/evalbench/container/case_runner.py"]

# Wall-clock grace added on top of the eval case timeout before Kubernetes
# kills the Job: image pull, uv startup and result upload all happen outside
# the window the case runner itself enforces.
DEFAULT_TIMEOUT_GRACE_SECONDS = 600.0
DEFAULT_CASE_TIMEOUT_SECONDS = 3600.0

# Env vars copied from the orchestrator into every case container when they are
# set. A case container inherits none of the eval server Deployment's
# environment, but the whole case -- simulated user and LLM judges included --
# now runs inside it, and `util.gcp` resolves the Vertex project/region from
# these when the model YAML does not name them. Without this, every case dies
# with "No GCP project_id found in config or environment variables."
#
# Deliberately config, not credentials: Job specs are readable by anyone with
# `get jobs` in the namespace, so passwords and API keys belong in `secrets:`
# or an explicit `env:` entry the operator has chosen to expose.
DEFAULT_INHERITED_ENV = (
    "EVAL_GCP_PROJECT_ID",
    "EVAL_GCP_PROJECT_REGION",
    "GOOGLE_CLOUD_PROJECT",
    "GOOGLE_CLOUD_LOCATION",
)


@dataclass
class WorkerPool:
    """One dedicated worker pool that eval cases can be scheduled onto."""

    name: str
    # Per-pool overrides; fall back to the top-level values when unset.
    max_concurrent: Optional[int] = None
    resources: Optional[dict] = None
    node_selector: dict = field(default_factory=dict)

    @classmethod
    def parse(cls, raw: Any) -> "WorkerPool":
        if isinstance(raw, str):
            return cls(name=raw)
        if not isinstance(raw, dict):
            raise ValueError(
                f"worker_pools entries must be a string or a mapping, got "
                f"{type(raw).__name__}: {raw!r}"
            )
        name = raw.get("name")
        if not name:
            raise ValueError(f"worker_pools entry is missing 'name': {raw!r}")
        return cls(
            name=str(name),
            max_concurrent=raw.get("max_concurrent"),
            resources=raw.get("resources"),
            node_selector=dict(raw.get("node_selector") or {}),
        )


@dataclass
class SecretMount:
    """A Secret to mount into every case container.

    Case containers get none of the eval server's volumes, so anything the
    generator reads off disk -- notably the service-account key Claude Code
    looks for at `/etc/evalbench-sa-key/key.json` -- has to be declared here.
    """

    name: str
    mount_path: str
    read_only: bool = True
    default_mode: int = 0o444
    optional: bool = False

    @classmethod
    def parse(cls, raw: Any) -> "SecretMount":
        if not isinstance(raw, dict):
            raise ValueError(
                f"containerization.secrets entries must be mappings with "
                f"'name' and 'mount_path', got {raw!r}")
        name, mount_path = raw.get("name"), raw.get("mount_path")
        if not name or not mount_path:
            raise ValueError(
                f"containerization.secrets entry needs both 'name' and "
                f"'mount_path': {raw!r}")
        return cls(
            name=str(name),
            mount_path=str(mount_path),
            read_only=bool(raw.get("read_only", True)),
            default_mode=int(raw.get("default_mode", 0o444)),
            optional=bool(raw.get("optional", False)),
        )


@dataclass
class ContainerizationConfig:
    """Settings for running each eval case in its own container.

    Populated from the run config::

        containerization:
          enabled: true
          worker_pools:
            - evalbench-worker-pool-1
            - name: evalbench-worker-pool-2
              max_concurrent: 4
          max_concurrent_per_pool: 8
    """

    enabled: bool = False
    backend: str = "gke"
    # Empty or "self": the eval server's own image, pinned by digest.
    image: str = ""
    namespace: str = DEFAULT_NAMESPACE
    service_account: str = DEFAULT_SERVICE_ACCOUNT
    worker_pools: list[WorkerPool] = field(default_factory=list)
    pool_selector_label: str = DEFAULT_POOL_LABEL
    max_concurrent_per_pool: int = 8
    resources: dict = field(default_factory=dict)
    env: dict = field(default_factory=dict)
    # Orchestrator env vars to copy into each case container. `env` wins on a
    # collision. Set to [] to disable inheritance entirely.
    inherit_env: list[str] = field(
        default_factory=lambda: list(DEFAULT_INHERITED_ENV))
    secrets: list[SecretMount] = field(default_factory=list)
    command: list[str] = field(default_factory=lambda: list(DEFAULT_CASE_COMMAND))
    # None derives it from the image: IfNotPresent for a digest (immutable,
    # so a node never needs to re-check the registry), Always for a tag.
    image_pull_policy: Optional[str] = None
    poll_interval_seconds: float = 5.0
    ttl_seconds_after_finished: int = 900
    # Kubernetes-side deadline. None means "derive from the eval case timeout".
    job_timeout_seconds: Optional[float] = None
    taint_key: str = DEFAULT_TAINT_KEY
    taint_value: str = DEFAULT_TAINT_VALUE
    # Keep failed Jobs and their ConfigMaps around for `kubectl describe`
    # until `ttl_seconds_after_finished` reaps them.
    keep_failed_jobs: bool = True
    # The agent never needs the Kubernetes API; Workload Identity works
    # without the token. Leaving it unmounted keeps the case's KSA rights
    # unreachable from inside the sandbox even if RBAC is later widened.
    automount_service_account_token: bool = False
    # Make each case Job a dependent of the eval server pod, so deleting or
    # replacing the pod garbage-collects its in-flight cases instead of
    # leaving them running (and spending tokens) with nobody to report to.
    bind_to_server_pod: bool = True
    image_pull_grace_seconds: float = DEFAULT_IMAGE_PULL_GRACE_SECONDS

    @property
    def pool_names(self) -> list[str]:
        return [p.name for p in self.worker_pools]

    @property
    def uses_self_image(self) -> bool:
        return not self.image or self.image == SELF_IMAGE

    def pull_policy_for(self, image: str) -> str:
        if self.image_pull_policy:
            return self.image_pull_policy
        return "IfNotPresent" if "@sha256:" in image else "Always"

    def deadline_seconds(self, case_timeout_seconds: Optional[float]) -> int:
        """Job `activeDeadlineSeconds` for a case with the given timeout."""
        if self.job_timeout_seconds is not None:
            return int(self.job_timeout_seconds)
        base = (
            case_timeout_seconds
            if case_timeout_seconds is not None
            else DEFAULT_CASE_TIMEOUT_SECONDS
        )
        return int(base + DEFAULT_TIMEOUT_GRACE_SECONDS)

    def pool_resources(self, pool: WorkerPool) -> dict:
        """Effective resource requirements for a case on `pool`.

        An explicit `ephemeral-storage` is always present in the result. It is
        not a nicety: a case pod pulls a ~900 MiB image and then has Claude
        Code npm-install itself into a fresh sandbox, and a node advertises
        only ~44 GiB of allocatable ephemeral storage. Without a request the
        scheduler treats that as zero and keeps packing pods until the disk
        fills, at which point the kubelet raises DiskPressure and starts
        evicting -- including whatever else shares the node. That is not
        theoretical; a 100-case run evicted the eval server this way.

        The request makes the scheduler stop at a safe density; the limit
        makes a runaway case die instead of taking the node down with it.
        """
        resources = pool.resources if pool.resources is not None else self.resources
        resources = copy.deepcopy(resources) if resources else {}
        for section, default in (
            ("requests", DEFAULT_EPHEMERAL_STORAGE_REQUEST),
            ("limits", DEFAULT_EPHEMERAL_STORAGE_LIMIT),
        ):
            bucket = resources.setdefault(section, {})
            bucket.setdefault("ephemeral-storage", default)
        return resources

    def pool_capacity(self, pool: WorkerPool) -> int:
        capacity = (
            pool.max_concurrent
            if pool.max_concurrent is not None
            else self.max_concurrent_per_pool
        )
        return max(1, int(capacity))

    @property
    def total_capacity(self) -> int:
        return sum(self.pool_capacity(p) for p in self.worker_pools)

    def resolved_env(self, environ: Optional[dict] = None) -> dict[str, str]:
        """Env for a case container: inherited vars, then explicit overrides."""
        environ = os.environ if environ is None else environ
        resolved = {
            name: environ[name]
            for name in self.inherit_env
            if environ.get(name) is not None
        }
        resolved.update({str(k): str(v) for k, v in self.env.items()})
        return resolved


def containerization_requested(config: Optional[dict]) -> bool:
    """Whether the run config opts into containerized execution.

    A cheap check for callers that only need to route (the gRPC server picks
    an executor with it); it does not validate the block.
    """
    raw = (config or {}).get("containerization")
    return isinstance(raw, dict) and bool(raw.get("enabled", False))


def load_containerization_config(config: dict) -> ContainerizationConfig:
    """Reads `containerization:` out of a run config.

    Returns a disabled config when the block is absent, so callers can always
    check `.enabled` instead of branching on presence.
    """
    raw = (config or {}).get("containerization") or {}
    if not isinstance(raw, dict):
        raise ValueError(
            f"`containerization` must be a mapping, got {type(raw).__name__}")

    if not raw.get("enabled", False):
        return ContainerizationConfig(enabled=False)

    backend = str(raw.get("backend", "gke")).lower()

    # Unset means SELF_IMAGE: the backend resolves the server's own image
    # digest, which also guarantees the case runner speaks the same spec
    # format as the orchestrator that built the spec.
    image = raw.get("image") or os.environ.get("EVALBENCH_CASE_IMAGE", "")

    pools = [WorkerPool.parse(p) for p in (raw.get("worker_pools") or [])]
    if not pools:
        raise ValueError(
            "containerization.enabled is true but `worker_pools` is empty. "
            "List the dedicated GKE node pools to spread eval cases over; "
            "create them with evalbench_service/k8s/worker_pools.sh."
        )
    duplicates = {n for n in [p.name for p in pools]
                  if [p.name for p in pools].count(n) > 1}
    if duplicates:
        raise ValueError(
            f"containerization.worker_pools has duplicate names: "
            f"{sorted(duplicates)}")

    cfg = ContainerizationConfig(
        enabled=True,
        backend=backend,
        image=image,
        namespace=raw.get("namespace") or DEFAULT_NAMESPACE,
        service_account=raw.get("service_account") or DEFAULT_SERVICE_ACCOUNT,
        worker_pools=pools,
        pool_selector_label=raw.get(
            "pool_selector_label") or DEFAULT_POOL_LABEL,
        max_concurrent_per_pool=int(raw.get("max_concurrent_per_pool", 8)),
        resources=dict(raw.get("resources") or {}),
        env=dict(raw.get("env") or {}),
        inherit_env=[
            str(v) for v in (
                raw["inherit_env"] if "inherit_env" in raw
                else DEFAULT_INHERITED_ENV
            )
        ],
        secrets=[SecretMount.parse(s) for s in (raw.get("secrets") or [])],
        command=list(raw.get("command") or DEFAULT_CASE_COMMAND),
        image_pull_policy=raw.get("image_pull_policy") or None,
        poll_interval_seconds=float(raw.get("poll_interval_seconds", 5)),
        ttl_seconds_after_finished=int(
            raw.get("ttl_seconds_after_finished", 900)),
        job_timeout_seconds=parse_timeout_seconds(raw.get("job_timeout")),
        taint_key=raw.get("taint_key", DEFAULT_TAINT_KEY),
        taint_value=raw.get("taint_value", DEFAULT_TAINT_VALUE),
        keep_failed_jobs=bool(raw.get("keep_failed_jobs", True)),
        automount_service_account_token=bool(
            raw.get("automount_service_account_token", False)),
        bind_to_server_pod=bool(raw.get("bind_to_server_pod", True)),
        image_pull_grace_seconds=float(raw.get(
            "image_pull_grace_seconds", DEFAULT_IMAGE_PULL_GRACE_SECONDS)),
    )

    logging.info(
        "Containerized eval execution enabled: backend=%s image=%s pools=%s "
        "capacity=%d",
        cfg.backend, cfg.image or SELF_IMAGE, cfg.pool_names,
        cfg.total_capacity,
    )
    return cfg
