"""Tests for the eval server's kubelet probe endpoint."""

import asyncio
import json
import socket
import threading
import time
import unittest
from unittest.mock import patch

from util.health import (
    HealthState,
    health_response,
    run_loop_heartbeat,
    start_health_server,
)


class _Clock:

    def __init__(self, now=100.0):
        self.now = now

    def __call__(self):
        return self.now


class TestHealthResponse(unittest.TestCase):

    def test_liveness_is_ok_before_the_first_heartbeat(self):
        self.assertEqual(health_response("/healthz", HealthState())[0], 200)

    def test_draining_does_not_fail_liveness(self):
        # Or the kubelet would kill the pod mid-shutdown instead of letting it
        # finish.
        state = HealthState()
        state.mark_draining()
        self.assertEqual(health_response("/healthz", state)[0], 200)

    def test_a_slow_loop_is_still_alive(self):
        clock = _Clock()
        state = HealthState(stall_seconds=300, clock=clock)
        state.beat()
        clock.now += 120  # the lag that got the server killed in the load test
        self.assertEqual(health_response("/healthz", state)[0], 200)
        self.assertEqual(health_response("/livez", state)[0], 200)

    def test_a_stalled_loop_fails_liveness(self):
        clock = _Clock()
        state = HealthState(stall_seconds=300, clock=clock)
        state.beat()
        clock.now += 301
        status, body = health_response("/healthz", state)
        self.assertEqual(status, 503)
        self.assertEqual(body["loop_idle_seconds"], 301.0)

    def test_not_ready_until_serving(self):
        state = HealthState()
        status, body = health_response("/readyz", state)
        self.assertEqual(status, 503)
        self.assertFalse(body["serving"])

    def test_ready_once_serving(self):
        state = HealthState()
        state.mark_serving()
        self.assertEqual(health_response("/readyz", state)[0], 200)

    def test_not_ready_once_draining(self):
        state = HealthState()
        state.mark_serving()
        state.mark_draining()
        status, body = health_response("/readyz", state)
        self.assertEqual(status, 503)
        self.assertTrue(body["draining"])

    def test_mark_draining_is_reentrant(self):
        # The SIGTERM handler may interrupt the main thread mid-`beat()`.
        state = HealthState()
        with state._lock:
            state.mark_draining()
        self.assertFalse(state.ready)

    def test_query_strings_are_ignored(self):
        self.assertEqual(health_response("/healthz?verbose=1", HealthState())[0], 200)

    def test_unknown_paths_are_404(self):
        self.assertEqual(health_response("/nope", HealthState())[0], 404)


class TestLoopHeartbeat(unittest.IsolatedAsyncioTestCase):

    async def test_beats_while_the_loop_runs(self):
        state = HealthState()
        task = asyncio.ensure_future(run_loop_heartbeat(state, interval=0.01))
        await asyncio.sleep(0.05)
        task.cancel()
        self.assertIsNotNone(state.stalled_for())
        self.assertLess(state.stalled_for(), 1)

    async def test_warns_when_the_loop_lags(self):
        state = HealthState()
        with patch("util.health.logging") as log:
            task = asyncio.ensure_future(run_loop_heartbeat(
                state, interval=0.01, lag_warn_seconds=0.05))
            await asyncio.sleep(0.02)
            time.sleep(0.2)  # block the loop
            await asyncio.sleep(0.05)
            task.cancel()
        self.assertTrue(log.warning.called)


class TestHealthServer(unittest.TestCase):

    def setUp(self):
        self.state = HealthState()
        self.server = start_health_server(self.state, port=0, host="127.0.0.1")
        self.addCleanup(self.server.close)

    def _request(self, raw: bytes, timeout: float = 5):
        with socket.create_connection(("127.0.0.1", self.server.port), timeout) as s:
            s.sendall(raw)
            chunks = []
            while True:
                data = s.recv(65536)
                if not data:
                    break
                chunks.append(data)
        head, _, body = b"".join(chunks).partition(b"\r\n\r\n")
        return int(head.split()[1]), body

    def _get(self, path, timeout: float = 5):
        return self._request(
            f"GET {path} HTTP/1.1\r\nHost: x\r\nUser-Agent: kube-probe/1.30\r\n\r\n"
            .encode(), timeout)

    def test_serves_liveness_over_http(self):
        status, body = self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

    def test_readiness_tracks_state(self):
        self.assertEqual(self._get("/readyz")[0], 503)
        self.state.mark_serving()
        self.assertEqual(self._get("/readyz")[0], 200)
        self.state.mark_draining()
        self.assertEqual(self._get("/readyz")[0], 503)

    def test_head_has_no_body(self):
        status, body = self._request(b"HEAD /healthz HTTP/1.1\r\n\r\n")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    def test_rejects_other_methods(self):
        status, _ = self._request(b"POST /healthz HTTP/1.1\r\n\r\n")
        self.assertEqual(status, 405)

    def test_a_garbage_request_does_not_break_the_server(self):
        try:
            self._request(b"\x00\xff garbage\r\n\r\n")
        except (ValueError, IndexError):
            pass  # a 400 or a dropped connection are both fine
        self.assertEqual(self._get("/healthz")[0], 200)

    def test_answers_while_an_event_loop_is_blocked(self):
        """Regression: probes shared the gRPC loop and died with it.

        In a 100-run load test the loop was starved long enough for every
        probe to time out, and the kubelet killed the server and every
        in-flight eval. The endpoint must not depend on that loop.
        """
        loop = asyncio.new_event_loop()
        blocked = threading.Event()

        def block_loop():
            async def hog():
                blocked.set()
                time.sleep(2)  # synchronous work on the loop
            loop.run_until_complete(hog())

        hog_thread = threading.Thread(target=block_loop)
        hog_thread.start()
        try:
            self.assertTrue(blocked.wait(5))
            started = time.monotonic()
            status, _ = self._get("/healthz", timeout=1)
            self.assertEqual(status, 200)
            self.assertLess(time.monotonic() - started, 1)
        finally:
            hog_thread.join()
            loop.close()


if __name__ == "__main__":
    unittest.main()
