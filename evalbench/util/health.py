"""Dependency-free HTTP health endpoint for the eval server.

The gRPC port is served with ALTS credentials in production, which a
Kubernetes `grpc:` probe cannot speak, and until now the Deployment had no
probe at all -- so `READY 1/1` only meant "the container process exists". That
is what let a broken container spec look healthy during the containerization
work. This module serves plain-HTTP paths on a separate port for the kubelet:

    /healthz, /livez  liveness. 200 unless the gRPC event loop has stopped
                      ticking for longer than `stall_seconds`. It never checks
                      downstream dependencies: a Vertex or database outage
                      must not get the pod killed and every in-flight eval
                      with it.
    /readyz           readiness. 200 only once the gRPC server is bound and
                      started, and 503 again as soon as graceful shutdown
                      begins.

The endpoint runs on its **own thread**, not on the gRPC server's event loop.
It used to share the loop, and a load test showed why that is wrong: when ~30
sessions finished at once, their reporting threads and the synchronous work
some gRPC handlers do on the loop starved it long enough that every probe
timed out, and the kubelet killed the server -- taking every in-flight eval
with it. A slow loop is a capacity problem, not a dead process. Liveness now
asks the narrower question "has the loop stopped entirely?" through a
heartbeat the loop itself refreshes (`run_loop_heartbeat`), with a threshold
measured in minutes rather than probe timeouts.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Callable, Optional
import asyncio
import json
import logging
import socket
import threading
import time

# How long the gRPC loop may go without a heartbeat before liveness fails.
DEFAULT_STALL_SECONDS = 300.0
# Loop lag worth a log line: well above normal scheduling jitter.
DEFAULT_LAG_WARN_SECONDS = 5.0
_HEARTBEAT_INTERVAL_SECONDS = 1.0


class HealthState:
    """What the probes report. Thread-safe; flipped by the server lifecycle."""

    def __init__(
        self,
        stall_seconds: float = DEFAULT_STALL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        # Reentrant: the SIGTERM handler calls `mark_draining` on the main
        # thread, which may be interrupted while already holding this lock.
        self._lock = threading.RLock()
        self._serving = False
        self._draining = False
        self._last_beat: Optional[float] = None
        self._stall_seconds = stall_seconds
        self._clock = clock

    def mark_serving(self) -> None:
        with self._lock:
            self._serving = True

    def mark_draining(self) -> None:
        with self._lock:
            self._draining = True

    def beat(self) -> None:
        """Records that the event loop just ran."""
        with self._lock:
            self._last_beat = self._clock()

    def stalled_for(self) -> Optional[float]:
        """Seconds since the last heartbeat, or None before the first one."""
        with self._lock:
            if self._last_beat is None:
                return None
            return self._clock() - self._last_beat

    @property
    def alive(self) -> bool:
        stalled = self.stalled_for()
        return stalled is None or stalled < self._stall_seconds

    @property
    def ready(self) -> bool:
        with self._lock:
            return self._serving and not self._draining

    def snapshot(self) -> dict:
        stalled = self.stalled_for()
        with self._lock:
            return {
                "serving": self._serving,
                "draining": self._draining,
                "loop_idle_seconds": (
                    round(stalled, 1) if stalled is not None else None),
            }


def health_response(path: str, state: HealthState) -> tuple[int, dict]:
    """Maps a request path to `(status, json_body)`."""
    path = (path or "/").split("?", 1)[0]
    if path in ("/healthz", "/livez"):
        if state.alive:
            return 200, {"status": "ok"}
        return 503, {"status": "event loop stalled", **state.snapshot()}
    if path == "/readyz":
        body = state.snapshot()
        if state.ready:
            return 200, {"status": "ready", **body}
        return 503, {"status": "not ready", **body}
    return 404, {"status": "not found", "path": path}


async def run_loop_heartbeat(
    state: HealthState,
    interval: float = _HEARTBEAT_INTERVAL_SECONDS,
    lag_warn_seconds: float = DEFAULT_LAG_WARN_SECONDS,
    clock: Callable[[], float] = time.monotonic,
) -> None:
    """Refreshes `state`'s heartbeat from the running loop, forever.

    Also logs whenever the loop wakes up much later than asked, which is the
    signal that something is blocking it -- worth knowing long before it is
    bad enough to fail liveness.
    """
    while True:
        state.beat()
        started = clock()
        await asyncio.sleep(interval)
        lag = clock() - started - interval
        if lag >= lag_warn_seconds:
            logging.warning(
                "health: event loop lagged %.1fs; something is blocking it", lag)


class _Handler(BaseHTTPRequestHandler):
    state: HealthState  # set on the per-server subclass

    def _respond(self, with_body: bool) -> None:
        status, body = health_response(self.path, self.state)
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        if with_body:
            self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802 (http.server naming)
        self._respond(with_body=True)

    def do_HEAD(self) -> None:  # noqa: N802
        self._respond(with_body=False)

    def _not_allowed(self) -> None:
        payload = json.dumps({"status": "method not allowed"}).encode("utf-8")
        self.send_response(405)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    do_POST = do_PUT = do_DELETE = do_PATCH = _not_allowed  # noqa: N815

    def log_message(self, format, *args) -> None:  # noqa: A002
        # A probe every few seconds is noise, not information.
        return


class _DualStackServer(ThreadingHTTPServer):
    daemon_threads = True
    address_family = socket.AF_INET6

    def server_bind(self) -> None:
        # Accept IPv4 too: the kubelet probes the pod's IPv4 address.
        try:
            self.socket.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 0)
        except (AttributeError, OSError):
            pass
        super().server_bind()


class HealthServer:
    """A running health endpoint; `close()` stops it."""

    def __init__(self, httpd: ThreadingHTTPServer, thread: threading.Thread):
        self._httpd = httpd
        self._thread = thread

    @property
    def port(self) -> int:
        return self._httpd.server_address[1]

    def close(self) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=5)


def start_health_server(
    state: HealthState, port: int, host: Optional[str] = None
) -> HealthServer:
    """Serves the probes on `host:port` from a dedicated daemon thread.

    `host=None` binds every interface, IPv4 and IPv6, which is what the
    kubelet needs to reach the pod IP. Raises OSError if the port is taken.
    """
    handler = type("HealthHandler", (_Handler,), {"state": state})
    httpd: ThreadingHTTPServer
    if host is not None and ":" not in host:
        # An explicit IPv4 address or hostname: an AF_INET6 socket can't
        # bind it.
        httpd = ThreadingHTTPServer((host, port), handler)
        httpd.daemon_threads = True
    else:
        try:
            httpd = _DualStackServer((host or "::", port), handler)
        except OSError:
            if host is not None:
                raise
            # No IPv6 on this host.
            httpd = ThreadingHTTPServer(("0.0.0.0", port), handler)
            httpd.daemon_threads = True
    thread = threading.Thread(
        target=httpd.serve_forever, name="health-endpoint", daemon=True)
    thread.start()
    logging.info("Health endpoint listening on port %d", httpd.server_address[1])
    return HealthServer(httpd, thread)
