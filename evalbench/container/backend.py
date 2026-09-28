"""Backend interface for running one eval case in one container."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import Any, Optional
import threading

from container.config import ContainerizationConfig, WorkerPool
from container.spec import EvalCaseSpec

# The case runner brackets its JSON payload with these so the orchestrator can
# recover the result from container logs regardless of what else the harness,
# uv, or the agent printed.
RESULT_BEGIN = "===EVALBENCH_CASE_RESULT_BEGIN==="
RESULT_END = "===EVALBENCH_CASE_RESULT_END==="


@dataclass
class CaseResult:
    """What one containerized eval case produced."""

    case_id: str
    # Rows the in-container AgentEvaluator appended, in the same shape the
    # in-process path produces, so reporting is unchanged.
    agent_results: list = field(default_factory=list)
    scoring_results: list = field(default_factory=list)
    # Set when the case never produced a result (scheduling failure, deadline,
    # crash). `agent_results` is then empty.
    error: Optional[str] = None
    pool: Optional[str] = None
    container_ref: Optional[str] = None
    logs: str = ""

    @property
    def ok(self) -> bool:
        return self.error is None

    @classmethod
    def from_payload(cls, case_id: str, payload: dict) -> "CaseResult":
        return cls(
            case_id=case_id,
            agent_results=payload.get("agent_results") or [],
            scoring_results=payload.get("scoring_results") or [],
            error=payload.get("error"),
        )


@dataclass
class CaseHandle:
    """Opaque reference to a submitted case, returned by `submit`."""

    case_id: str
    pool: str
    name: str
    backend_state: Any = None


class ContainerBackend(ABC):
    """Runs eval cases as containers on some compute substrate."""

    def __init__(self, config: ContainerizationConfig) -> None:
        self.config = config

    @abstractmethod
    def submit(
        self,
        spec: EvalCaseSpec,
        pool: WorkerPool,
        deadline_seconds: int,
    ) -> CaseHandle:
        """Starts the case. Returns as soon as it is accepted, not finished."""

    @abstractmethod
    def wait(self, handle: CaseHandle, timeout_seconds: Optional[float] = None) -> CaseResult:
        """Blocks until the case finishes (or times out) and collects its result."""

    @abstractmethod
    def cleanup(self, handle: CaseHandle, succeeded: bool) -> None:
        """Releases backend resources for a finished case."""

    def close(self) -> None:
        """Releases backend-wide resources. Safe to call more than once."""

    def __enter__(self) -> "ContainerBackend":
        return self

    def __exit__(self, exc_type, exc_value, exc_tb) -> None:
        self.close()


def get_backend(config: ContainerizationConfig) -> ContainerBackend:
    """Instantiates the backend named by `config.backend`."""
    if config.backend in ("gke", "k8s", "kubernetes"):
        # Imported lazily: the kubernetes client is only needed when the GKE
        # backend is actually selected.
        from container.k8s_backend import KubernetesJobBackend

        return KubernetesJobBackend(config)
    raise ValueError(
        f"Unknown containerization backend {config.backend!r}. Supported: gke."
    )


# Backends shared across concurrent eval sessions, keyed by
# "<backend>/<namespace>", with a refcount so the last session out closes it.
_SHARED_BACKENDS: dict[str, tuple[ContainerBackend, int]] = {}
_SHARED_BACKENDS_LOCK = threading.Lock()


def acquire_shared_backend(config: ContainerizationConfig) -> ContainerBackend:
    """Returns a process-wide backend for `config`, creating it if needed.

    Every concurrent `Eval` RPC builds its own evaluator, and a Kubernetes
    backend is not a cheap object: it loads cluster credentials and owns an
    HTTP connection pool to the API server. Hundreds of sessions each holding
    their own would multiply both. The backends are stateless with respect to
    an individual case -- everything case-specific travels in the `CaseHandle`
    -- so one per (backend, namespace) is safe to share.

    Every call must be paired with `release_shared_backend`.
    """
    key = f"{config.backend}/{config.namespace}"
    with _SHARED_BACKENDS_LOCK:
        existing = _SHARED_BACKENDS.get(key)
        if existing is not None:
            backend, refs = existing
            _SHARED_BACKENDS[key] = (backend, refs + 1)
            return backend
        backend = get_backend(config)
        _SHARED_BACKENDS[key] = (backend, 1)
        return backend


def release_shared_backend(backend: ContainerBackend) -> None:
    """Drops one reference taken by `acquire_shared_backend`.

    Closes the backend only once nothing else is using it, so one session
    finishing does not pull the API client out from under the sessions still
    polling their Jobs.
    """
    with _SHARED_BACKENDS_LOCK:
        for key, (candidate, refs) in list(_SHARED_BACKENDS.items()):
            if candidate is not backend:
                continue
            if refs > 1:
                _SHARED_BACKENDS[key] = (candidate, refs - 1)
                return
            del _SHARED_BACKENDS[key]
            break
        else:
            # Not shared (a test double, or an explicitly injected backend).
            return
    backend.close()


def reset_shared_backends() -> None:
    """Drops the shared-backend registry without closing. For tests."""
    with _SHARED_BACKENDS_LOCK:
        _SHARED_BACKENDS.clear()


def extract_result_payload(logs: str) -> Optional[dict]:
    """Pulls the sentinel-delimited JSON result out of container logs.

    Returns None when the markers are absent or the payload does not parse --
    both mean the case died before reporting, and the caller turns that into a
    `CaseResult` with an error.
    """
    import json

    begin = logs.rfind(RESULT_BEGIN)
    if begin == -1:
        return None
    start = begin + len(RESULT_BEGIN)
    end = logs.find(RESULT_END, start)
    if end == -1:
        return None
    try:
        return json.loads(logs[start:end].strip())
    except json.JSONDecodeError:
        return None
