"""Round-robin placement of eval cases across worker pools."""

from contextlib import contextmanager
from typing import Iterator, Optional
import itertools
import logging
import threading

from container.config import ContainerizationConfig, WorkerPool

# Per-pool slots shared by every router in the process, keyed by
# "<namespace>/<pool>". See `shared_slots`.
_SHARED_SLOTS: dict[str, threading.Semaphore] = {}
_SHARED_SLOT_CAPACITY: dict[str, int] = {}
_SHARED_SLOTS_LOCK = threading.Lock()


def shared_slots(
    config: ContainerizationConfig,
) -> dict[str, threading.Semaphore]:
    """Returns the process-wide slot semaphores for `config`'s pools.

    The eval server handles many `Eval` RPCs concurrently and each one builds
    its own evaluator and router. With per-router semaphores, N in-flight
    sessions would each be free to dispatch `max_concurrent_per_pool` cases at
    the same node pool, so the cluster sees N x the configured concurrency and
    the autoscaler is asked for N x the nodes. Keying the semaphores on
    (namespace, pool) at module scope makes the limit mean what it says
    regardless of how many sessions are running.

    The first caller to ask for a pool fixes its capacity; a later session that
    configures the same pool differently is warned and gets the existing limit,
    since silently taking the larger of the two would defeat the cap.
    """
    slots: dict[str, threading.Semaphore] = {}
    with _SHARED_SLOTS_LOCK:
        for pool in config.worker_pools:
            key = f"{config.namespace}/{pool.name}"
            capacity = config.pool_capacity(pool)
            if key not in _SHARED_SLOTS:
                _SHARED_SLOTS[key] = threading.Semaphore(capacity)
                _SHARED_SLOT_CAPACITY[key] = capacity
            elif _SHARED_SLOT_CAPACITY[key] != capacity:
                logging.warning(
                    "Worker pool %s is already capped at %d concurrent case(s) "
                    "process-wide by an earlier eval session; ignoring this "
                    "run's %d. Restart the eval server to change it.",
                    key, _SHARED_SLOT_CAPACITY[key], capacity,
                )
            slots[pool.name] = _SHARED_SLOTS[key]
    return slots


def reset_shared_slots() -> None:
    """Drops the process-wide slot registry. For tests."""
    with _SHARED_SLOTS_LOCK:
        _SHARED_SLOTS.clear()
        _SHARED_SLOT_CAPACITY.clear()


class WorkerPoolRouter:
    """Hands out worker pools round-robin, respecting per-pool capacity.

    Each pool has its own slot count, so a pool backed by four nodes does not
    get handed the same number of concurrent cases as one backed by twenty.
    `acquire()` walks the pools starting from the round-robin cursor and takes
    the first free slot, so a saturated pool is skipped rather than blocking a
    case that a neighbouring pool could run right now. When every pool is full
    the caller blocks on the pool the cursor landed on.

    `slots` lets callers inject the semaphores instead of owning them. The
    server passes `shared_slots(config)` so that concurrent eval sessions share
    one budget; the default of private semaphores keeps a standalone router
    (and its tests) self-contained.
    """

    def __init__(
        self,
        config: ContainerizationConfig,
        slots: Optional[dict[str, threading.Semaphore]] = None,
    ) -> None:
        if not config.worker_pools:
            raise ValueError("WorkerPoolRouter requires at least one worker pool")
        self._config = config
        self._pools: list[WorkerPool] = list(config.worker_pools)
        if slots is None:
            slots = {
                pool.name: threading.Semaphore(config.pool_capacity(pool))
                for pool in self._pools
            }
        missing = [p.name for p in self._pools if p.name not in slots]
        if missing:
            raise ValueError(
                f"WorkerPoolRouter was given slots that do not cover every "
                f"configured pool; missing: {missing}")
        self._slots = slots
        self._cursor = itertools.count()
        self._lock = threading.Lock()
        self._in_flight = {pool.name: 0 for pool in self._pools}

    @property
    def pools(self) -> list[WorkerPool]:
        return list(self._pools)

    @property
    def capacity(self) -> int:
        return self._config.total_capacity

    def in_flight(self) -> dict[str, int]:
        with self._lock:
            return dict(self._in_flight)

    @contextmanager
    def acquire(self) -> Iterator[WorkerPool]:
        """Reserves a slot on some pool for the duration of the block."""
        pool = self._acquire_slot()
        with self._lock:
            self._in_flight[pool.name] += 1
        try:
            yield pool
        finally:
            with self._lock:
                self._in_flight[pool.name] -= 1
            self._slots[pool.name].release()

    def _acquire_slot(self) -> WorkerPool:
        start = next(self._cursor) % len(self._pools)
        ordered = self._pools[start:] + self._pools[:start]

        for pool in ordered:
            if self._slots[pool.name].acquire(blocking=False):
                return pool

        # Every pool is saturated. Queue on the one the cursor picked so waiters
        # spread out instead of all piling onto the first pool.
        pool = ordered[0]
        logging.debug(
            "All worker pools are at capacity; queuing on %s", pool.name)
        self._slots[pool.name].acquire()
        return pool
