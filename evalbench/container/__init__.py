"""Containerized eval execution: one container per eval case.

The default agent path runs every scenario of an evalset as subprocesses inside
the single eval-server pod, sharing one sandboxed ``fake_home``. This package
runs each scenario in its own short-lived container instead, spread round-robin
over dedicated GKE worker (node) pools.

Layout::

    config.py       `containerization:` run-config block
    spec.py         the self-contained description of one eval case
    pool_router.py  round-robin placement across worker pools
"""

from container.config import ContainerizationConfig
from container.pool_router import WorkerPoolRouter
from container.spec import EvalCaseSpec

__all__ = [
    "ContainerizationConfig",
    "EvalCaseSpec",
    "WorkerPoolRouter",
]
