import datetime
import json
import os
import shutil
import tempfile
import threading
import unittest
import unittest.mock

import yaml

from container.backend import (
    RESULT_BEGIN,
    RESULT_END,
    CaseHandle,
    CaseResult,
    ContainerBackend,
    acquire_shared_backend,
    extract_result_payload,
    release_shared_backend,
    reset_shared_backends,
)
from container.config import ContainerizationConfig, WorkerPool
from container.pool_router import reset_shared_slots
from dataset.evalgeminicliinput import EvalGeminiCliRequest
from evaluator.containeragentevaluator import ContainerAgentEvaluator


class FakeBackend(ContainerBackend):
    """Records submissions and returns canned results, no cluster involved."""

    def __init__(self, config, results=None, fail_case_ids=()):
        super().__init__(config)
        self.submissions = []
        self.cleanups = []
        self.closed = False
        self._results = results or {}
        self._fail_case_ids = set(fail_case_ids)
        self._lock = threading.Lock()

    def submit(self, spec, pool, deadline_seconds):
        with self._lock:
            self.submissions.append((spec, pool.name, deadline_seconds))
        return CaseHandle(
            case_id=spec.case_id, pool=pool.name, name=f"job-{spec.case_id}")

    def wait(self, handle, timeout_seconds=None):
        if handle.case_id in self._fail_case_ids:
            return CaseResult(
                case_id=handle.case_id,
                error="Case container produced no result payload.",
                pool=handle.pool,
                container_ref=handle.name,
            )
        return self._results.get(
            handle.case_id,
            CaseResult(
                case_id=handle.case_id,
                agent_results=[{"eval_id": handle.case_id}],
                scoring_results=[{"id": handle.case_id, "score": 1}],
                pool=handle.pool,
                container_ref=handle.name,
            ),
        )

    def cleanup(self, handle, succeeded):
        with self._lock:
            self.cleanups.append((handle.name, succeeded))

    def close(self):
        self.closed = True


class _EvaluatorTestBase(unittest.TestCase):

    def setUp(self):
        # Pool slots are process-wide, so a budget taken by one test would
        # otherwise carry into the next and block it.
        reset_shared_slots()
        self.addCleanup(reset_shared_slots)

        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.model_path = os.path.join(self.tmp, "claude.yaml")
        with open(self.model_path, "w") as f:
            yaml.safe_dump({"generator": "claude_code", "model": "opus"}, f)

        self.containerization = ContainerizationConfig(
            enabled=True,
            image="img:latest",
            worker_pools=[WorkerPool(name="pool-a"), WorkerPool(name="pool-b")],
            max_concurrent_per_pool=2,
        )
        self.config = {
            "orchestrator": "agent",
            "model_config": self.model_path,
            "eval_case_timeout": 60,
        }

    def _evaluator(self, backend=None, config=None):
        backend = backend or FakeBackend(self.containerization)
        evaluator = ContainerAgentEvaluator(
            config or self.config, self.containerization, backend)
        return evaluator, backend

    @staticmethod
    def _dataset(scenario_ids):
        payload = {
            "scenarios": [
                {"id": sid, "starting_prompt": f"do {sid}"} for sid in scenario_ids
            ]
        }
        return [EvalGeminiCliRequest(id="0", payload=json.dumps(payload))]


class TestGeneratorValidation(_EvaluatorTestBase):

    def test_rejects_unsupported_generator(self):
        path = os.path.join(self.tmp, "gemini.yaml")
        with open(path, "w") as f:
            yaml.safe_dump({"generator": "gemini_cli"}, f)
        with self.assertRaisesRegex(ValueError, "gemini_cli"):
            ContainerAgentEvaluator(
                {"model_config": path}, self.containerization, FakeBackend(
                    self.containerization))

    def test_rejects_non_path_model_config(self):
        with self.assertRaisesRegex(ValueError, "model_config"):
            ContainerAgentEvaluator(
                {"model_config": {"generator": "claude_code"}},
                self.containerization,
                FakeBackend(self.containerization),
            )

    def test_rejects_disabled_containerization(self):
        with self.assertRaisesRegex(ValueError, "containerization.enabled"):
            ContainerAgentEvaluator(
                self.config, ContainerizationConfig(enabled=False))


class TestScenarioExpansion(_EvaluatorTestBase):

    def test_splits_a_bundled_evalset_into_one_case_each(self):
        scenarios = ContainerAgentEvaluator._expand_scenarios(
            self._dataset(["a", "b", "c"]))
        self.assertEqual([s["id"] for s in scenarios], ["a", "b", "c"])

    def test_accepts_a_bare_scenario_payload(self):
        item = EvalGeminiCliRequest(
            id="0", payload=json.dumps({"id": "solo", "starting_prompt": "hi"}))
        scenarios = ContainerAgentEvaluator._expand_scenarios([item])
        self.assertEqual([s["id"] for s in scenarios], ["solo"])

    def test_accepts_an_already_decoded_payload(self):
        item = EvalGeminiCliRequest(
            id="0", payload={"scenarios": [{"id": "x"}]})
        scenarios = ContainerAgentEvaluator._expand_scenarios([item])
        self.assertEqual([s["id"] for s in scenarios], ["x"])

    def test_skips_unparseable_payloads_without_failing_the_run(self):
        good = EvalGeminiCliRequest(id="1", payload=json.dumps({"id": "ok"}))
        bad = EvalGeminiCliRequest(id="2", payload="{not json")
        scenarios = ContainerAgentEvaluator._expand_scenarios([bad, good])
        self.assertEqual([s["id"] for s in scenarios], ["ok"])


class TestEvaluate(_EvaluatorTestBase):

    def test_submits_one_container_per_scenario(self):
        evaluator, backend = self._evaluator()
        evaluator.evaluate(self._dataset(["a", "b", "c"]), "job-1", _now())
        self.assertEqual(len(backend.submissions), 3)
        self.assertEqual(
            sorted(s[0].case_id for s in backend.submissions), ["a", "b", "c"])

    def test_spreads_cases_round_robin_over_pools(self):
        evaluator, backend = self._evaluator()
        evaluator.evaluate(self._dataset(["a", "b", "c", "d"]), "job-1", _now())
        pools = sorted(s[1] for s in backend.submissions)
        self.assertEqual(pools, ["pool-a", "pool-a", "pool-b", "pool-b"])

    def test_aggregates_results_from_every_container(self):
        evaluator, backend = self._evaluator()
        outputs, scores = evaluator.evaluate(
            self._dataset(["a", "b"]), "job-1", _now())
        self.assertEqual(sorted(o["eval_id"] for o in outputs), ["a", "b"])
        self.assertEqual(sorted(s["id"] for s in scores), ["a", "b"])

    def test_passes_the_case_deadline_to_the_backend(self):
        evaluator, backend = self._evaluator()
        evaluator.evaluate(self._dataset(["a"]), "job-1", _now())
        _, _, deadline = backend.submissions[0]
        # eval_case_timeout 60s + the default 10m grace.
        self.assertEqual(deadline, 660)

    def test_scenario_timeout_overrides_the_run_timeout(self):
        evaluator, backend = self._evaluator()
        payload = {
            "scenarios": [
                {"id": "a", "starting_prompt": "x", "eval_case_timeout": "5m"}
            ]
        }
        evaluator.evaluate(
            [EvalGeminiCliRequest(id="0", payload=json.dumps(payload))],
            "job-1", _now())
        self.assertEqual(backend.submissions[0][2], 300 + 600)

    def test_a_failed_case_becomes_a_failure_row_not_a_silent_drop(self):
        backend = FakeBackend(self.containerization, fail_case_ids={"b"})
        evaluator, _ = self._evaluator(backend)
        outputs, scores = evaluator.evaluate(
            self._dataset(["a", "b"]), "job-1", _now())

        self.assertEqual(len(outputs), 2)
        failure = next(o for o in outputs if o["eval_id"] == "b")
        self.assertEqual(failure["returncode"], 1)
        self.assertIn("no result payload", failure["stderr"])
        self.assertEqual(failure["job_id"], "job-1")
        # The healthy case still contributes its score; the failed one does not.
        self.assertEqual([s["id"] for s in scores], ["a"])

    def test_a_failure_row_links_the_sandbox_the_case_uploaded(self):
        backend = FakeBackend(
            self.containerization,
            results={
                "b": CaseResult(
                    case_id="b",
                    error="agent crashed",
                    artifact_uri="gs://bkt/results/job-1/b.zip",
                )
            },
        )
        evaluator, _ = self._evaluator(backend)
        outputs, _ = evaluator.evaluate(self._dataset(["b"]), "job-1", _now())
        self.assertEqual(
            outputs[0]["artifact_uri"], "gs://bkt/results/job-1/b.zip")
        self.assertIsNone(outputs[0]["fake_home"])

    def test_cleans_up_every_container(self):
        backend = FakeBackend(self.containerization, fail_case_ids={"b"})
        evaluator, _ = self._evaluator(backend)
        evaluator.evaluate(self._dataset(["a", "b"]), "job-1", _now())
        self.assertEqual(
            sorted(backend.cleanups), [("job-a", True), ("job-b", False)])

    def test_empty_dataset_short_circuits(self):
        evaluator, backend = self._evaluator()
        outputs, scores = evaluator.evaluate([], "job-1", _now())
        self.assertEqual((outputs, scores), ([], []))
        self.assertEqual(backend.submissions, [])

    def test_specs_carry_the_inlined_model_config(self):
        evaluator, backend = self._evaluator()
        evaluator.evaluate(self._dataset(["a"]), "job-1", _now())
        spec = backend.submissions[0][0]
        self.assertEqual(spec.job_id, "job-1")
        self.assertIn("generator: claude_code", "".join(spec.model_files.values()))
        self.assertNotIn("containerization", spec.config)

    def test_a_backend_that_raises_does_not_sink_the_run(self):
        class ExplodingBackend(FakeBackend):
            def wait(self, handle, timeout_seconds=None):
                if handle.case_id == "b":
                    raise RuntimeError("api down")
                return super().wait(handle, timeout_seconds)

        backend = ExplodingBackend(self.containerization)
        evaluator, _ = self._evaluator(backend)
        outputs, scores = evaluator.evaluate(
            self._dataset(["a", "b"]), "job-1", _now())

        self.assertEqual(len(outputs), 2)
        failure = next(o for o in outputs if o["eval_id"] == "b")
        self.assertIn("api down", failure["stderr"])
        self.assertEqual([s["id"] for s in scores], ["a"])


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
            backend = FakeBackend(config)
            self.created.append(backend)
            return backend

        patcher = unittest.mock.patch(
            "container.backend.get_backend", side_effect=fake_get_backend)
        patcher.start()
        self.addCleanup(patcher.stop)

    @staticmethod
    def _config(namespace="ns"):
        return ContainerizationConfig(
            enabled=True,
            image="img:latest",
            namespace=namespace,
            worker_pools=[WorkerPool(name="pool-a")],
        )

    def test_reuses_one_backend_across_sessions(self):
        cfg = self._config()
        first = acquire_shared_backend(cfg)
        second = acquire_shared_backend(cfg)
        self.assertIs(first, second)
        self.assertEqual(len(self.created), 1)

    def test_separate_namespaces_get_separate_backends(self):
        first = acquire_shared_backend(self._config("ns-1"))
        second = acquire_shared_backend(self._config("ns-2"))
        self.assertIsNot(first, second)
        self.assertEqual(len(self.created), 2)

    def test_closes_only_after_the_last_release(self):
        cfg = self._config()
        backend = acquire_shared_backend(cfg)
        acquire_shared_backend(cfg)

        release_shared_backend(backend)
        # A session finishing must not pull the client out from under the
        # sessions still polling their Jobs.
        self.assertFalse(backend.closed)

        release_shared_backend(backend)
        self.assertTrue(backend.closed)

    def test_reacquire_after_full_release_builds_a_new_backend(self):
        cfg = self._config()
        first = acquire_shared_backend(cfg)
        release_shared_backend(first)
        second = acquire_shared_backend(cfg)
        self.assertIsNot(first, second)

    def test_releasing_an_unshared_backend_is_a_noop(self):
        # Injected test doubles never entered the registry; releasing one must
        # not close it or raise.
        standalone = FakeBackend(self._config())
        release_shared_backend(standalone)
        self.assertFalse(standalone.closed)


def _now():
    return datetime.datetime(2026, 9, 10, 12, 0, 0)


if __name__ == "__main__":
    unittest.main()
