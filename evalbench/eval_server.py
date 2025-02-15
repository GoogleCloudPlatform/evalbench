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

_LOCALHOST = flags.DEFINE_bool(
    "localhost",
    False,
    "Whether to use localhost. ALTS is only available on GCP, so this is useful"
    " for local testing.",
)

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
    if _LOCALHOST.value:
        # --localhost is for testing purpose. Use insecure_server_credentials()
        # because local creds does not work between a client running on the host
        # and a server running inside a container on the same host.
        logging.info("Using localhost server insecure credentials per flag")
        server.add_insecure_port("[::]:50051")
    else:
        logging.info("Using ALTS server credentials")
        creds = grpc.alts_server_credentials()
        server.add_secure_port("[::]:50051", creds)
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
