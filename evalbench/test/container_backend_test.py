"""Tests for the container backend interface and its shared registry."""

import time
import unittest
import unittest.mock

from container.backend import (
    RESULT_BEGIN,
    RESULT_END,
    CaseHandle,
    CaseResult,
    ContainerBackend,
    acquire_shared_backend,
    extract_result_payload,
    get_backend,
    release_shared_backend,
    reset_shared_backends,
)
from container.config import ContainerizationConfig, WorkerPool


class _ClosableBackend(ContainerBackend):
    """The smallest concrete backend: records whether it was closed."""

    def __init__(self, config):
        super().__init__(config)
        self.closed = False

    def submit(self, spec, pool, deadline_seconds):
        raise NotImplementedError

    def wait(self, handle, timeout_seconds=None, cancel_event=None):
        raise NotImplementedError

    def cleanup(self, handle, succeeded):
        raise NotImplementedError

    def close(self):
        self.closed = True


def _config(namespace="ns"):
    return ContainerizationConfig(
        enabled=True,
        image="img:latest",
        namespace=namespace,
        worker_pools=[WorkerPool(name="pool-a")],
    )


class TestCaseResult(unittest.TestCase):

    def test_from_payload_reads_every_field(self):
        result = CaseResult.from_payload("a", {
            "agent_results": [{"eval_id": "a"}],
            "scoring_results": [{"id": 1}],
            "agent_version": "claude@2.1.0",
            "error": None,
        })
        self.assertTrue(result.ok)
        self.assertEqual(result.agent_results, [{"eval_id": "a"}])
        self.assertEqual(result.scoring_results, [{"id": 1}])
        self.assertEqual(result.agent_version, "claude@2.1.0")

    def test_from_payload_tolerates_missing_and_null_lists(self):
        result = CaseResult.from_payload("a", {"agent_results": None})
        self.assertEqual(result.agent_results, [])
        self.assertEqual(result.scoring_results, [])
        self.assertIsNone(result.agent_version)

    def test_a_reported_error_makes_the_result_not_ok(self):
        result = CaseResult.from_payload("a", {"error": "boom"})
        self.assertFalse(result.ok)
        self.assertEqual(result.error, "boom")


class TestCaseHandle(unittest.TestCase):

    def test_records_when_it_was_submitted_and_starts_not_abandoned(self):
        before = time.monotonic()
        handle = CaseHandle(case_id="a", pool="p", name="job-a")
        self.assertGreaterEqual(handle.submitted_at, before)
        self.assertFalse(handle.abandoned)


class TestGetBackend(unittest.TestCase):

    def test_an_unknown_backend_is_a_clear_error(self):
        cfg = _config()
        cfg.backend = "nomad"
        with self.assertRaisesRegex(ValueError, "Supported: gke"):
            get_backend(cfg)


class TestBackendContextManager(unittest.TestCase):

    def test_closes_on_exit(self):
        with _ClosableBackend(_config()) as backend:
            self.assertFalse(backend.closed)
        self.assertTrue(backend.closed)


class TestExtractResultPayload(unittest.TestCase):

    def test_extracts_payload_between_sentinels(self):
        logs = (
            "some harness noise\n"
            f"{RESULT_BEGIN}\n"
            '{"case_id": "a", "agent_results": [1]}\n'
            f"{RESULT_END}\n"
            "trailing noise\n"
        )
        self.assertEqual(
            extract_result_payload(logs),
            {"case_id": "a", "agent_results": [1]},
        )

    def test_returns_none_without_markers(self):
        self.assertIsNone(extract_result_payload("just logs"))

    def test_returns_none_on_truncated_output(self):
        self.assertIsNone(extract_result_payload(f"{RESULT_BEGIN}\n{{\"a\": 1}}"))

    def test_returns_none_on_invalid_json(self):
        self.assertIsNone(
            extract_result_payload(f"{RESULT_BEGIN}\nnot json\n{RESULT_END}"))

    def test_uses_the_last_payload_when_the_marker_repeats(self):
        logs = (
            f"{RESULT_BEGIN}\n{{\"case_id\": \"first\"}}\n{RESULT_END}\n"
            f"{RESULT_BEGIN}\n{{\"case_id\": \"second\"}}\n{RESULT_END}\n"
        )
        self.assertEqual(extract_result_payload(logs)["case_id"], "second")


class TestSharedBackend(unittest.TestCase):
    """One API client per (backend, namespace), not one per eval session."""

    def setUp(self):
        reset_shared_backends()
        self.addCleanup(reset_shared_backends)
        self.created = []

        def fake_get_backend(config):
            backend = _ClosableBackend(config)
            self.created.append(backend)
            return backend

        patcher = unittest.mock.patch(
            "container.backend.get_backend", side_effect=fake_get_backend)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_reuses_one_backend_across_sessions(self):
        cfg = _config()
        first = acquire_shared_backend(cfg)
        second = acquire_shared_backend(cfg)
        self.assertIs(first, second)
        self.assertEqual(len(self.created), 1)

    def test_separate_namespaces_get_separate_backends(self):
        first = acquire_shared_backend(_config("ns-1"))
        second = acquire_shared_backend(_config("ns-2"))
        self.assertIsNot(first, second)
        self.assertEqual(len(self.created), 2)

    def test_closes_only_after_the_last_release(self):
        cfg = _config()
        backend = acquire_shared_backend(cfg)
        acquire_shared_backend(cfg)

        release_shared_backend(backend)
        # A session finishing must not pull the client out from under the
        # sessions still polling their Jobs.
        self.assertFalse(backend.closed)

        release_shared_backend(backend)
        self.assertTrue(backend.closed)

    def test_reacquire_after_full_release_builds_a_new_backend(self):
        cfg = _config()
        first = acquire_shared_backend(cfg)
        release_shared_backend(first)
        second = acquire_shared_backend(cfg)
        self.assertIsNot(first, second)

    def test_releasing_an_unshared_backend_is_a_noop(self):
        # Injected test doubles never entered the registry; releasing one must
        # not close it or raise.
        standalone = _ClosableBackend(_config())
        release_shared_backend(standalone)
        self.assertFalse(standalone.closed)


if __name__ == "__main__":
    unittest.main()
