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


async def _serve():
    """Starts the server."""
    logging.info("Starting server")

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
