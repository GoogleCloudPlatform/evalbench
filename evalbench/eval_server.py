"""Server on GCP side for the evaluation service."""

import asyncio
from collections.abc import Sequence

from absl import app
from absl import flags
from absl import logging
import grpc
import util
from eval_service import EvalServicer
from eval_service import SessionManagerInterceptor
from evalproto import eval_service_pb2_grpc
import os
import sys

_LOCALHOST = flags.DEFINE_bool(
    "localhost",
    False,
    "Whether to use localhost. ALTS is only available on GCP, so this is useful"
    " for local testing.",
)

CLOUD_RUN = os.getenv("CLOUD_RUN", False)
PORT = os.getenv("PORT", 50051)
_cleanup_coroutines = []


class UncloseableStream:

    def __init__(self, stream):
        self.stream = stream

    def write(self, data):
        self.stream.write(data)

    def flush(self):
        self.stream.flush()

    def close(self):
        pass  # Do not close the underlying stream


async def _serve():
    """Starts the server."""
    # Prevent stream closing
    logging.get_absl_handler().python_handler.stream = UncloseableStream(sys.stdout)
    logging.info("Starting server")

    interceptors = [
        SessionManagerInterceptor("SessionManagerInterceptor"),
    ]

    server = grpc.aio.server(interceptors=interceptors)
    servicer = EvalServicer()
    eval_service_pb2_grpc.add_EvalServiceServicer_to_server(servicer, server)
    if _LOCALHOST.value or CLOUD_RUN:
        logging.info("Using localhost server insecure credentials per flag")
        server.add_insecure_port("[::]:%s" % PORT)
    else:
        logging.info("Using ALTS server credentials")
        creds = grpc.alts_server_credentials()
        server.add_secure_port("[::]:%s" % PORT, creds)
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

    logging.use_absl_handler()

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
