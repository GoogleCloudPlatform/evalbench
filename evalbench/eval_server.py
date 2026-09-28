import os
import sys
from absl import logging

import logging as py_logging
from util.context import rpc_id_var


# --- Logging Initialization (MUST happen before other imports) ---


class UncloseableStream:
    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        self.stream.write(data)

    def flush(self):
        self.stream.flush()

    def close(self):
        pass  # Do not close the underlying stream


class SessionIdFilter(py_logging.Filter):
    def filter(self, record):
        record.session_id = rpc_id_var.get()
        return True


logging.use_absl_handler()
python_handler = logging.get_absl_handler().python_handler
python_handler.stream = UncloseableStream(sys.stdout)

formatter = py_logging.Formatter(
    '%(asctime)s [%(session_id)s] %(levelname)s '
    '%(filename)s:%(lineno)d: %(message)s'
)
python_handler.setFormatter(formatter)
python_handler.addFilter(SessionIdFilter())


# --- Remaining Imports ---
import asyncio
import concurrent.futures
from collections.abc import Sequence

from absl import app
from absl import flags
import grpc
import util
from eval_service import EvalServicer
from eval_service import SessionManagerInterceptor
from evalproto import eval_service_pb2_grpc

_LOCALHOST = flags.DEFINE_bool(
    "localhost",
    False,
    "Whether to use localhost. ALTS is only available on GCP, so this is "
    "useful for local testing.",
)

CLOUD_RUN = os.getenv("CLOUD_RUN", False)
PORT = os.getenv("PORT", 50051)
_cleanup_coroutines = []


def _eval_executor_size() -> int:
    """How many `Eval` RPCs this pod may run at once.

    `EvalServicer.Eval` hands the whole evaluation to
    `loop.run_in_executor(None, ...)`, so one in-flight RPC occupies one thread
    of the loop's default executor for the entire run -- minutes to hours.
    CPython sizes that pool at `min(32, cpu_count + 4)`, which silently caps a
    20-CPU pod at ~32 concurrent evaluations no matter how much headroom the
    cluster has. Callers that drive one scenario per RPC (the continuous CI
    fan-out does) hit that wall long before they hit the pod's CPU.

    The right size depends on what the threads actually do:

    - `containerization.enabled: true` -- the thread only builds a case spec
      and polls a Kubernetes Job. It is idle almost the whole time, the real
      work is out on the worker pools, and this can safely be in the hundreds.
    - in-process execution -- the thread runs the agent CLI, the simulated
      user and the scorers on *this* pod, so oversubscribing it just thrashes.

    Hence the conservative default and the explicit knob.
    """
    configured = os.getenv("EVALBENCH_EVAL_THREADS")
    if configured:
        try:
            size = int(configured)
        except ValueError:
            logging.error(
                "Ignoring EVALBENCH_EVAL_THREADS=%r: not an integer.", configured)
        else:
            if size > 0:
                return size
            logging.error(
                "Ignoring EVALBENCH_EVAL_THREADS=%d: must be positive.", size)
    return min(32, (os.cpu_count() or 1) + 4)


async def _serve():
    """Starts the server."""
    logging.info("Starting server")

    # `Eval` offloads onto the loop's default executor, so sizing it is what
    # actually sets this pod's concurrent-evaluation limit.
    eval_threads = _eval_executor_size()
    eval_executor = concurrent.futures.ThreadPoolExecutor(
        max_workers=eval_threads, thread_name_prefix="evalbench-eval"
    )
    asyncio.get_running_loop().set_default_executor(eval_executor)
    logging.info("Eval executor sized to %d concurrent evaluation(s)",
                 eval_threads)

    interceptors = [
        SessionManagerInterceptor("SessionManagerInterceptor"),
    ]

    server = grpc.aio.server(interceptors=interceptors)
    servicer = EvalServicer()
    eval_service_pb2_grpc.add_EvalServiceServicer_to_server(servicer, server)
    host = os.getenv("EVALBENCH_HOST", "[::]")
    if _LOCALHOST.value or CLOUD_RUN:
        logging.info("Using localhost server insecure credentials per flag")
        bound_port = server.add_insecure_port(f"{host}:{PORT}")
    else:
        logging.info("Using ALTS server credentials")
        creds = grpc.alts_server_credentials()
        bound_port = server.add_secure_port(f"{host}:{PORT}", creds)

    if bound_port == 0:
        raise RuntimeError(f"Failed to bind to port {PORT} on host {host}!")
    await server.start()
    logging.info("Server started")

    async def server_graceful_shutdown():
        logging.info("Starting graceful shutdown...")
        await server.stop(5)

    _cleanup_coroutines.append(server_graceful_shutdown())
    await server.wait_for_termination()


def main(argv: Sequence[str]) -> None:
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")

    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        loop.run_until_complete(_serve())
    except KeyboardInterrupt:
        util.get_SessionManager().shutdown()
    finally:
        loop.run_until_complete(asyncio.gather(*_cleanup_coroutines))
        loop.close()


if __name__ == "__main__":
    app.run(main)
