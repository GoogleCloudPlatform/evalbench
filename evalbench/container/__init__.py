"""Containerized eval execution: one container per eval case.

The default agent path runs every scenario of an evalset as subprocesses inside
the single eval-server pod, sharing one sandboxed ``fake_home``. This package
runs each scenario in its own short-lived container instead, spread round-robin
over dedicated GKE worker (node) pools.

Layout::

    config.py       `containerization:` run-config block
    spec.py         the self-contained description of one eval case
    pool_router.py  round-robin placement across worker pools
    backend.py      ContainerBackend interface + CaseResult
    case_runner.py  the in-container entrypoint that runs one case
    cancellation.py lets a cancelled RPC stop the cases it started
"""

from container.backend import CaseResult, ContainerBackend, get_backend
from container.config import ContainerizationConfig
from container.pool_router import WorkerPoolRouter
from container.spec import EvalCaseSpec

__all__ = [
    "CaseResult",
    "ContainerBackend",
    "ContainerizationConfig",
    "EvalCaseSpec",
    "WorkerPoolRouter",
    "get_backend",
]
