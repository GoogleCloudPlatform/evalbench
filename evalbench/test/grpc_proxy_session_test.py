"""Tests for the session queues of the gRPC proxy model."""

import contextvars
import queue
import threading
import unittest
from types import SimpleNamespace

from generators.models.grpc_proxy import PROXY_QUEUES, GrpcProxyModel
from util.context import rpc_id_var


class GrpcProxySessionTest(unittest.TestCase):

    def setUp(self):
        PROXY_QUEUES.clear()
        self.addCleanup(PROXY_QUEUES.clear)
        self._stop = threading.Event()
        self.addCleanup(self._stop.set)

    def _start_session(self, session_id):
        """Adds a session and a client thread that answers its requests."""
        inboxes, out_queue = {}, queue.Queue()
        PROXY_QUEUES[session_id] = (inboxes, out_queue)

        def client():
            while not self._stop.is_set():
                try:
                    request = out_queue.get(timeout=0.1)
                except queue.Empty:
                    continue
                inboxes[request.conversation_id].put(SimpleNamespace(
                    generated_sql=f"sql from {session_id}",
                    generated_nl_response="",
                ))

        threading.Thread(target=client, daemon=True).start()

    @staticmethod
    def _generate(model, conv_id="c1"):
        return model.generate({"id": conv_id, "nl_prompt": "q"})["generated_sql"]

    def test_model_uses_the_queues_of_its_session(self):
        self._start_session("a")
        self._start_session("b")

        for session_id in ("b", "a"):
            model = GrpcProxyModel({"session_id": session_id})
            self.assertEqual(self._generate(model), f"sql from {session_id}")

    def test_model_without_a_session_id_uses_the_rpc_id(self):
        self._start_session("a")
        self._start_session("b")
        model = GrpcProxyModel({})

        def run():
            rpc_id_var.set("b")
            return self._generate(model)

        self.assertEqual(contextvars.copy_context().run(run), "sql from b")

    def test_model_without_identity_uses_the_only_session(self):
        self._start_session("a")

        self.assertEqual(self._generate(GrpcProxyModel({})), "sql from a")

    def test_model_without_identity_fails_with_two_sessions(self):
        self._start_session("a")
        self._start_session("b")

        with self.assertRaisesRegex(RuntimeError, "2 sessions"):
            self._generate(GrpcProxyModel({}))

    def test_model_fails_after_its_session_ended(self):
        self._start_session("a")

        with self.assertRaisesRegex(RuntimeError, "not active"):
            self._generate(GrpcProxyModel({"session_id": "ended"}))


if __name__ == "__main__":
    unittest.main()
