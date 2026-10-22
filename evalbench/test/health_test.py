"""Tests for the eval server's liveness/readiness endpoints."""

import asyncio
import time
import unittest
import urllib.error
import urllib.request

from util import health


def _get(port, path):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}{path}", timeout=5) as r:
            return r.status, r.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


class TestHealthState(unittest.TestCase):

    def test_not_live_before_the_first_beat(self):
        self.assertFalse(health.HealthState().live()[0])

    def test_live_after_a_beat(self):
        state = health.HealthState()
        state.beat()
        self.assertTrue(state.live()[0])

    def test_a_stale_heartbeat_is_not_live(self):
        state = health.HealthState(stale_after_seconds=0.01)
        state.beat()
        time.sleep(0.05)
        live, detail = state.live()
        self.assertFalse(live)
        self.assertIn("old", detail)

    def test_ready_needs_both_serving_and_a_live_loop(self):
        state = health.HealthState()
        state.set_serving(True)
        self.assertFalse(state.ready()[0], "no heartbeat yet")
        state.beat()
        self.assertTrue(state.ready()[0])
        state.set_serving(False)
        self.assertFalse(state.ready()[0])

    def test_heartbeat_task_beats(self):
        state = health.HealthState()

        async def run():
            task = asyncio.create_task(health.run_heartbeat(state, interval=0.01))
            await asyncio.sleep(0.03)
            task.cancel()

        asyncio.run(run())
        self.assertTrue(state.live()[0])


class TestHealthServer(unittest.TestCase):

    def setUp(self):
        self.state = health.HealthState()
        self.server = health.start_health_server(self.state, port=0)
        self.addCleanup(self.server.shutdown)
        self.port = self.server.server_address[1]

    def test_endpoints_report_503_until_healthy(self):
        self.assertEqual(_get(self.port, "/healthz")[0], 503)
        self.assertEqual(_get(self.port, "/readyz")[0], 503)

    def test_endpoints_report_200_when_healthy(self):
        self.state.beat()
        self.state.set_serving(True)
        self.assertEqual(_get(self.port, "/healthz")[0], 200)
        status, body = _get(self.port, "/readyz")
        self.assertEqual(status, 200)
        self.assertIn("serving", body)

    def test_liveness_does_not_depend_on_serving(self):
        # A server still starting up must not be killed for not serving yet.
        self.state.beat()
        self.assertEqual(_get(self.port, "/healthz")[0], 200)
        self.assertEqual(_get(self.port, "/readyz")[0], 503)

    def test_unknown_path_is_404(self):
        self.assertEqual(_get(self.port, "/nope")[0], 404)

    def test_an_unbindable_port_returns_none_instead_of_raising(self):
        self.assertIsNone(health.start_health_server(self.state, port=self.port))


if __name__ == "__main__":
    unittest.main()
