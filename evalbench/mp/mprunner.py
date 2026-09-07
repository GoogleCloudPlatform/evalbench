"""Multiprocessing runner."""

import concurrent.futures
import contextvars
from typing import Any

from work import work


def do_work(work_obj: work.Work, item_config: Any = None) -> Any:
    """Do the work.

    Args:
      work_obj: The work object.
      item_config: The config for the work item.

    Returns:
      The result of the work.
    """
    return work_obj.run(item_config)


class MPRunner:
    """Multi-processing class that implements threadpool execution of work.

    The runner owns a `ThreadPoolExecutor`, whose worker threads have no idle
    timeout: once started they stay alive, blocked on the pool's internal work
    queue, until the pool is shut down or the interpreter exits. Callers must
    therefore release the runner when they are done with it. Exiting a `with`
    block runs every submitted work item to completion and then releases the
    threads::

        with MPRunner(10) as runner:
            runner.execute_work(work_obj)
            ...

    A caller that must abandon work in progress, such as one that has already
    timed a stage out, calls `shutdown()` directly instead.

    Runners that are created per sub-dataset and never released leak their
    worker threads for the remaining lifetime of the process.

    Attributes:

      executor: The thread pool backing this runner.
      futures: The futures of every work item submitted so far.
    """

    def __init__(self, concurrent_tests: int = 10) -> None:
        """Initialize the class.

        Args:
          concurrent_tests:
        """
        self.executor = concurrent.futures.ThreadPoolExecutor(concurrent_tests)
        self.futures = []

    def execute_work(self, work_obj: work.Work) -> None:
        """Schedule to requested work.

        Args:
          work_obj: The work object.
        """
        ctx = contextvars.copy_context()
        self.futures.append(self.executor.submit(ctx.run, do_work, work_obj))

    def shutdown(self, wait: bool = False, cancel_futures: bool = True) -> None:
        """Release the pool's worker threads.

        Idle workers exit as soon as they pick up the shutdown sentinel. A
        worker that is still executing a work item exits once that item
        returns. The runner is spent afterwards: `execute_work` raises
        `RuntimeError`, so a caller that runs more than once needs a fresh
        runner per run.

        Both defaults are the opposite of `Executor.shutdown`, which waits and
        keeps queued work. They suit a caller that has already collected the
        results it wants and now only needs the threads back.

        Args:
          wait: Whether to block until every running work item has finished.
            Defaults to False so a work item that has hung (and that the caller
            has already abandoned, e.g. after a stage timeout) cannot stall the
            rest of the evaluation.
          cancel_futures: Whether to cancel work items that are queued but have
            not started running. Defaults to True, since the caller is done
            with this runner and any remaining queued work is dead work.
        """
        self.executor.shutdown(wait=wait, cancel_futures=cancel_futures)

    def __enter__(self) -> "MPRunner":
        return self

    def __exit__(self, exc_type, exc_value, exc_traceback) -> None:
        # Matches `Executor.__exit__`: run everything that was submitted and
        # block until it finishes. A caller that wants the abandoning
        # behaviour asks for it by calling `shutdown()` directly.
        self.shutdown(wait=True, cancel_futures=False)
