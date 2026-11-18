"""HTTP liveness and readiness endpoints for the eval server.

The gRPC port cannot back a Kubernetes probe: it speaks ALTS, which the
kubelet's gRPC probe does not, and a TCP probe only proves the port is bound.
Before this the Deployment had no probe at all, so `READY 1/1` meant nothing
more than "the container process exists" -- a server with a wedged event loop,
or one that never finished starting, looked healthy indefinitely.

    GET /healthz  200 while the asyncio loop is still turning (liveness)
    GET /readyz   200 once the gRPC server is serving and the loop is live

Liveness is a loop heartbeat rather than a process check because supervisord
already restarts a process that exits; the failure it cannot see is a process
that is alive but no longer servicing RPCs.
"""

from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Optional
import asyncio
import logging
import os
import threading
import time

DEFAULT_PORT = 8000
HEARTBEAT_INTERVAL_SECONDS = 5.0
# Long enough that a slow synchronous step on the loop (a large EvalConfig
# resource write) is not mistaken for a hang; the probe's own
# failureThreshold adds more slack on top.
DEFAULT_STALE_AFTER_SECONDS = 60.0


class HealthState:
    """What the endpoints report. Thread-safe; written from the loop."""

    def __init__(self, stale_after_seconds: float = DEFAULT_STALE_AFTER_SECONDS):
        self.stale_after_seconds = stale_after_seconds
        self._lock = threading.Lock()
        self._last_beat: Optional[float] = None
        self._serving = False

    def beat(self) -> None:
        with self._lock:
            self._last_beat = time.monotonic()

    def set_serving(self, serving: bool) -> None:
        with self._lock:
            self._serving = serving

    def live(self) -> tuple[bool, str]:
        with self._lock:
            last = self._last_beat
        if last is None:
            return False, "event loop heartbeat not started"
        age = time.monotonic() - last
        if age > self.stale_after_seconds:
            return False, f"event loop heartbeat is {age:.0f}s old"
        return True, f"event loop heartbeat {age:.1f}s ago"

    def ready(self) -> tuple[bool, str]:
        live, detail = self.live()
        if not live:
            return False, detail
        with self._lock:
            serving = self._serving
        if not serving:
            return False, "gRPC server not serving"
        return True, "serving"


async def run_heartbeat(
    state: HealthState, interval: float = HEARTBEAT_INTERVAL_SECONDS
) -> None:
    """Beats while the loop is responsive. Run as a task on the server loop."""
    while True:
        state.beat()
        await asyncio.sleep(interval)


def _make_handler(state: HealthState):
    class _Handler(BaseHTTPRequestHandler):
        def do_GET(self):  # noqa: N802 (http.server API)
            if self.path == "/healthz":
                ok, detail = state.live()
            elif self.path == "/readyz":
                ok, detail = state.ready()
            else:
                self.send_error(404)
                return
            body = (("ok: " if ok else "unhealthy: ") + detail + "\n").encode()
            self.send_response(200 if ok else 503)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format, *args):
            # Probes hit this every few seconds; keep them out of the logs.
            pass

    return _Handler


def start_health_server(
    state: HealthState, port: Optional[int] = None
) -> Optional[ThreadingHTTPServer]:
    """Serves the endpoints from a daemon thread.

    Returns None (and logs) if the port cannot be bound, rather than taking
    the eval server down over its health endpoint.
    """
    if port is None:
        port = int(os.getenv("EVALBENCH_HEALTH_PORT", DEFAULT_PORT))
    try:
        server = ThreadingHTTPServer(("", port), _make_handler(state))
    except OSError as e:
        logging.error("Health endpoints disabled: cannot bind port %d: %s", port, e)
        return None
    server.daemon_threads = True
    thread = threading.Thread(
        target=server.serve_forever, name="evalbench-health", daemon=True)
    thread.start()
    logging.info("Health endpoints serving on :%d (/healthz, /readyz)", port)
    return server
