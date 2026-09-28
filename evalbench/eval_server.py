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
import signal
from collections.abc import Sequence

from absl import app
from absl import flags
import grpc
import util
from eval_service import EvalServicer
from eval_service import SessionManagerInterceptor
from evalproto import eval_service_pb2_grpc
from util.health import HealthState, start_health_server

_LOCALHOST = flags.DEFINE_bool(
    "localhost",
    False,
    "Whether to use localhost. ALTS is only available on GCP, so this is "
    "useful for local testing.",
)

CLOUD_RUN = os.getenv("CLOUD_RUN", False)
PORT = os.getenv("PORT", 50051)
# Plain-HTTP port for kubelet probes; the gRPC port is ALTS. See util/health.py.
_DEFAULT_HEALTH_PORT = 8080
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

    health_state = HealthState()
    health_server = await _start_health_endpoint(health_state)

    await server.start()
    health_state.mark_serving()
    logging.info("Server started")

    async def server_graceful_shutdown():
        logging.info("Starting graceful shutdown...")
        health_state.mark_draining()
        await server.stop(_shutdown_grace_seconds())
        if health_server is not None:
            health_server.close()

    _cleanup_coroutines.append(server_graceful_shutdown())
    _install_sigterm_handler(server, health_state)
    await server.wait_for_termination()


def _shutdown_grace_seconds() -> float:
    try:
        return float(os.getenv("EVALBENCH_SHUTDOWN_GRACE_SECONDS", "5"))
    except ValueError:
        return 5.0


async def _start_health_endpoint(state: HealthState):
    """Starts the kubelet probe endpoint, unless disabled with port 0.

    A bind failure is logged rather than fatal: locally the port may simply be
    taken, and in the cluster a missing endpoint already surfaces as a failing
    probe.
    """
    try:
        port = int(os.getenv("EVALBENCH_HEALTH_PORT", str(_DEFAULT_HEALTH_PORT)))
    except ValueError:
        logging.error("Invalid EVALBENCH_HEALTH_PORT; health endpoint disabled.")
        return None
    if port <= 0:
        logging.info("Health endpoint disabled (EVALBENCH_HEALTH_PORT=%d).", port)
        return None
    if str(port) == str(PORT):
        # Cloud Run injects PORT=8080; never fight the gRPC server for it.
        logging.warning(
            "Health port %d equals the gRPC port; health endpoint disabled.", port)
        return None
    try:
        return await start_health_server(state, port)
    except OSError as e:
        logging.error("Could not start health endpoint on port %d: %s", port, e)
        return None


def _install_sigterm_handler(server, state: HealthState) -> None:
    """Drains on SIGTERM instead of dying mid-request.

    The kubelet sends SIGTERM on pod deletion. Python's default handler exits
    immediately, so readiness never went false and in-flight RPCs were cut
    without a status. Flip readiness first, then stop the server with the
    configured grace; `wait_for_termination` then returns and `main` runs the
    rest of the cleanup.
    """
    def _on_sigterm():
        logging.info("SIGTERM received; draining.")
        state.mark_draining()
        asyncio.ensure_future(server.stop(_shutdown_grace_seconds()))

    try:
        asyncio.get_running_loop().add_signal_handler(signal.SIGTERM, _on_sigterm)
    except (NotImplementedError, RuntimeError):
        # Not on the main thread, or a platform without loop signal support.
        logging.debug("SIGTERM handler not installed", exc_info=True)


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
