"""Server on GCP side for the evaluation service."""

import asyncio
from collections.abc import Sequence

from absl import app
from absl import flags
from absl import logging
import grpc

import eval_service
import eval_service_pb2_grpc

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
    server = grpc.aio.server()
    servicer = eval_service.EvalServicer()
    eval_service_pb2_grpc.add_EvalServiceServicer_to_server(servicer, server)
    if _LOCALHOST.value:
        # --localhost is for testing purpose. Use insecure_server_credentials()
        # because local creds does not work between a client running on the host
        # and a server running inside a container on the same host.
        logging.info("Using localhost server insecure credentials per flag")
        creds = grpc.insecure_server_credentials()
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
    loop = asyncio.get_event_loop()
    try:
        loop.run_until_complete(_serve())
    finally:
        loop.run_until_complete(asyncio.gather(*_cleanup_coroutines))
        loop.close()


if __name__ == "__main__":
    app.run(main)
