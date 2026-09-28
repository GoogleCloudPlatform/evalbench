"""Dependency-free HTTP health endpoint for the eval server.

The gRPC port is served with ALTS credentials in production, which a
Kubernetes `grpc:` probe cannot speak, and until now the Deployment had no
probe at all -- so `READY 1/1` only meant "the container process exists". That
is what let a broken container spec look healthy during the containerization
work. This module serves two plain-HTTP paths on a separate port for the
kubelet:

    /healthz  liveness. Answered from the server's event loop, so a 200 proves
              the loop is still scheduling work. It never checks downstream
              dependencies: a Vertex or database outage must not get the pod
              killed and every in-flight eval with it.
    /readyz   readiness. 200 only once the gRPC server is bound and started,
              and 503 again as soon as graceful shutdown begins.

It is built on `asyncio.start_server` rather than a web framework so it adds
no dependency and shares the gRPC server's event loop.
"""

import asyncio
import json
import logging
import threading
from typing import Optional

_READ_TIMEOUT_SECONDS = 5.0
_MAX_HEADER_LINES = 100


class HealthState:
    """What the probes report. Thread-safe; flipped by the server lifecycle."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._serving = False
        self._draining = False

    def mark_serving(self) -> None:
        with self._lock:
            self._serving = True

    def mark_draining(self) -> None:
        with self._lock:
            self._draining = True

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._serving and not self._draining

    def snapshot(self) -> dict:
        with self._lock:
            return {"serving": self._serving, "draining": self._draining}


def health_response(path: str, state: HealthState) -> tuple[int, dict]:
    """Maps a request path to `(status, json_body)`."""
    path = (path or "/").split("?", 1)[0]
    if path in ("/healthz", "/livez"):
        return 200, {"status": "ok"}
    if path == "/readyz":
        body = state.snapshot()
        if state.ready:
            return 200, {"status": "ready", **body}
        return 503, {"status": "not ready", **body}
    return 404, {"status": "not found", "path": path}


_REASONS = {200: "OK", 404: "Not Found", 405: "Method Not Allowed",
            503: "Service Unavailable"}


async def _handle(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    state: HealthState,
) -> None:
    try:
        request_line = await asyncio.wait_for(
            reader.readline(), _READ_TIMEOUT_SECONDS)
        parts = request_line.decode("latin-1").split()
        method = parts[0] if parts else ""
        path = parts[1] if len(parts) > 1 else "/"
        # Drain the headers; the probe sends a handful and we need none.
        for _ in range(_MAX_HEADER_LINES):
            line = await asyncio.wait_for(
                reader.readline(), _READ_TIMEOUT_SECONDS)
            if line in (b"\r\n", b"\n", b""):
                break

        if method not in ("GET", "HEAD"):
            status, body = 405, {"status": "method not allowed"}
        else:
            status, body = health_response(path, state)

        payload = json.dumps(body).encode("utf-8")
        head = (
            f"HTTP/1.1 {status} {_REASONS.get(status, '')}\r\n"
            f"Content-Type: application/json\r\n"
            f"Content-Length: {len(payload)}\r\n"
            f"Connection: close\r\n\r\n"
        ).encode("latin-1")
        writer.write(head if method == "HEAD" else head + payload)
        await writer.drain()
    except (asyncio.TimeoutError, ConnectionError):
        pass
    except Exception:  # never let a malformed probe take the loop down
        logging.debug("health: error serving probe", exc_info=True)
    finally:
        try:
            writer.close()
            await writer.wait_closed()
        except Exception:
            pass


async def start_health_server(
    state: HealthState,
    port: int,
    host: Optional[str] = None,
) -> asyncio.AbstractServer:
    """Starts serving the probes on `host:port` in the running event loop.

    `host=None` binds every interface, IPv4 and IPv6, which is what the
    kubelet needs to reach the pod IP.
    """
    server = await asyncio.start_server(
        lambda r, w: _handle(r, w, state), host=host, port=port)
    logging.info("Health endpoint listening on port %d", port)
    return server
