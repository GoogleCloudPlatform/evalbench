"""The thread pool that long-running evaluations run on in the gRPC server.

Every evaluation entry point (`Eval`, `Interact`, `AgentInteract`) offloads
its blocking work -- running the orchestrator, then processing results -- to
a thread, and holds that thread for the whole run: minutes to hours.

Those used to go to the event loop's *default* executor, which has two
problems:

- Its size is CPython's `min(32, cpu_count + 4)`. That silently capped the
  server at 32 concurrent evaluations however much CPU the pod or cluster
  had, and callers driving one scenario per RPC hit it first.
- It is shared with `asyncio.to_thread`, which `Interact`/`AgentInteract` use
  to poll their outbound queues. Once long evaluations held every thread, a
  stream could not poll its own queue -- and its evaluation was waiting on
  that queue.

So evaluations get a dedicated, explicitly sized pool, and the default
executor is left for short work. When the pool is full the next evaluation
queues, and that is logged instead of silently stretching the RPC's
latency.
"""

from typing import Any, Callable, Optional
import asyncio
import concurrent.futures
import logging
import os
import threading


def _default_size() -> int:
    # The same bound the default executor applied, so moving onto a dedicated
    # pool does not by itself change how much in-process work runs at once.
    return min(32, (os.cpu_count() or 1) + 4)


def size_from_env(var: str, default: int) -> int:
    configured = os.getenv(var)
    if not configured:
        return default
    try:
        size = int(configured)
    except ValueError:
        logging.error("Ignoring %s=%r: not an integer.", var, configured)
        return default
    if size <= 0:
        logging.error("Ignoring %s=%d: must be positive.", var, size)
        return default
    return size


class EvalExecutor:
    """A named thread pool that reports when callers start to queue."""

    def __init__(self, name: str, max_workers: int) -> None:
        self.name = name
        self.max_workers = max_workers
        self._pool = concurrent.futures.ThreadPoolExecutor(
            max_workers=max_workers, thread_name_prefix=name)
        self._lock = threading.Lock()
        self._in_flight = 0

    @property
    def in_flight(self) -> int:
        with self._lock:
            return self._in_flight

    def run(self, fn: Callable[..., Any], *args: Any) -> "asyncio.Future[Any]":
        """Schedules `fn(*args)` on the pool from inside the running loop."""
        with self._lock:
            self._in_flight += 1
            in_flight = self._in_flight
        if in_flight > self.max_workers:
            logging.warning(
                "%s: %d evaluation(s) in flight for %d thread(s); this one is "
                "queued until a thread frees up.",
                self.name, in_flight, self.max_workers)

        def tracked() -> Any:
            try:
                return fn(*args)
            finally:
                with self._lock:
                    self._in_flight -= 1

        return asyncio.get_running_loop().run_in_executor(self._pool, tracked)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)


_EVAL_EXECUTOR: Optional[EvalExecutor] = None
_EVAL_EXECUTOR_LOCK = threading.Lock()


def get_eval_executor() -> EvalExecutor:
    """The process-wide pool for evaluations, sized by EVALBENCH_EVAL_THREADS."""
    global _EVAL_EXECUTOR
    with _EVAL_EXECUTOR_LOCK:
        if _EVAL_EXECUTOR is None:
            size = size_from_env("EVALBENCH_EVAL_THREADS", _default_size())
            _EVAL_EXECUTOR = EvalExecutor("evalbench-eval", size)
            logging.info(
                "Evaluation executor sized to %d concurrent evaluation(s)", size)
        return _EVAL_EXECUTOR

