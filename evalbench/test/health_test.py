"""Tests for the eval server's kubelet probe endpoint."""

import asyncio
import json
import unittest

from util.health import HealthState, health_response, start_health_server


class TestHealthResponse(unittest.TestCase):

    def test_liveness_is_always_ok(self):
        state = HealthState()
        self.assertEqual(health_response("/healthz", state)[0], 200)
        state.mark_draining()
        # Draining must not fail liveness, or the kubelet would kill the pod
        # mid-shutdown instead of letting it finish.
        self.assertEqual(health_response("/healthz", state)[0], 200)

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

    def test_query_strings_are_ignored(self):
        self.assertEqual(health_response("/healthz?verbose=1", HealthState())[0], 200)

    def test_unknown_paths_are_404(self):
        self.assertEqual(health_response("/nope", HealthState())[0], 404)


class TestHealthServer(unittest.IsolatedAsyncioTestCase):

    async def asyncSetUp(self):
        self.state = HealthState()
        self.server = await start_health_server(self.state, port=0, host="127.0.0.1")
        self.port = self.server.sockets[0].getsockname()[1]

    async def asyncTearDown(self):
        self.server.close()
        await self.server.wait_closed()

    async def _request(self, raw: bytes):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(raw)
        await writer.drain()
        response = await asyncio.wait_for(reader.read(), 5)
        writer.close()
        head, _, body = response.partition(b"\r\n\r\n")
        status = int(head.split()[1])
        return status, body

    async def _get(self, path):
        return await self._request(
            f"GET {path} HTTP/1.1\r\nHost: x\r\nUser-Agent: kube-probe/1.30\r\n\r\n"
            .encode())

    async def test_serves_liveness_over_http(self):
        status, body = await self._get("/healthz")
        self.assertEqual(status, 200)
        self.assertEqual(json.loads(body)["status"], "ok")

    async def test_readiness_tracks_state(self):
        self.assertEqual((await self._get("/readyz"))[0], 503)
        self.state.mark_serving()
        self.assertEqual((await self._get("/readyz"))[0], 200)
        self.state.mark_draining()
        self.assertEqual((await self._get("/readyz"))[0], 503)

    async def test_head_has_no_body(self):
        status, body = await self._request(b"HEAD /healthz HTTP/1.1\r\n\r\n")
        self.assertEqual(status, 200)
        self.assertEqual(body, b"")

    async def test_rejects_other_methods(self):
        status, _ = await self._request(b"POST /healthz HTTP/1.1\r\n\r\n")
        self.assertEqual(status, 405)

    async def test_a_garbage_request_does_not_break_the_server(self):
        reader, writer = await asyncio.open_connection("127.0.0.1", self.port)
        writer.write(b"\x00\xff garbage\r\n\r\n")
        await writer.drain()
        await asyncio.wait_for(reader.read(), 5)
        writer.close()
        self.assertEqual((await self._get("/healthz"))[0], 200)


if __name__ == "__main__":
    unittest.main()
