"""Tests for the GKE Job backend.

The Kubernetes API calls are mocked, but the request bodies are the real
`V1Job` / `V1ConfigMap` models, so these assert on the manifest the cluster
would actually receive -- above all that each case lands on the worker pool it
was routed to.
"""

import os
import shutil
import tempfile
import unittest
from unittest.mock import MagicMock, patch

import yaml

from container.backend import (
    RESULT_BEGIN,
    RESULT_END,
    CaseHandle,
    extract_result_payload,
)
from container.config import ContainerizationConfig, SecretMount, WorkerPool
from container.spec import CASE_DIR, EvalCaseSpec


def _make_backend(config):
    """Builds a KubernetesJobBackend with its API clients mocked out."""
    from container.k8s_backend import KubernetesJobBackend

    with patch("kubernetes.config.load_incluster_config"), patch(
        "kubernetes.client.BatchV1Api"
    ) as batch, patch("kubernetes.client.CoreV1Api") as core:
        backend = KubernetesJobBackend(config)
    backend._batch = batch.return_value
    backend._core = core.return_value
    return backend


def _job_body(backend):
    return backend._batch.create_namespaced_job.call_args.kwargs["body"]


def _configmap_body(backend):
    return backend._core.create_namespaced_config_map.call_args.kwargs["body"]


def _pod(name="pod-1", phase="Succeeded"):
    pod = MagicMock()
    pod.metadata.name = name
    pod.status.phase = phase
    pod.status.conditions = []
    pod.status.container_statuses = []
    return pod


def _job_status(succeeded=None, failed=None, conditions=None):
    job = MagicMock()
    job.status.succeeded = succeeded
    job.status.failed = failed
    job.status.conditions = conditions or []
    return job


class _BackendTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        model_path = os.path.join(self.tmp, "claude.yaml")
        with open(model_path, "w") as f:
            yaml.safe_dump({"generator": "claude_code"}, f)

        self.config = ContainerizationConfig(
            enabled=True,
            image="img:tag",
            namespace="evalbench-namespace",
            service_account="evalbench-ksa",
            worker_pools=[WorkerPool(name="pool-a"), WorkerPool(name="pool-b")],
            resources={"requests": {"cpu": "2"}, "limits": {"cpu": "4"}},
            poll_interval_seconds=0,
        )
        self.spec = EvalCaseSpec.build(
            scenario={"id": "CUJ 01", "starting_prompt": "hi"},
            config={"model_config": model_path, "orchestrator": "agent"},
            job_id="0f8c2b1a-1111",
            run_time_iso="2026-09-10T12:00:00",
        )
        self.backend = _make_backend(self.config)


class TestSubmit(_BackendTestBase):

    def test_pins_the_job_to_the_requested_worker_pool(self):
        self.backend.submit(self.spec, WorkerPool(name="pool-b"), 900)
        pod_spec = _job_body(self.backend).spec.template.spec
        self.assertEqual(
            pod_spec.node_selector["cloud.google.com/gke-nodepool"], "pool-b")

    def test_merges_per_pool_node_selector(self):
        pool = WorkerPool(name="pool-b", node_selector={"disktype": "ssd"})
        self.backend.submit(self.spec, pool, 900)
        pod_spec = _job_body(self.backend).spec.template.spec
        self.assertEqual(pod_spec.node_selector["disktype"], "ssd")
        self.assertEqual(
            pod_spec.node_selector["cloud.google.com/gke-nodepool"], "pool-b")

    def test_tolerates_the_dedicated_worker_taint(self):
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        toleration = _job_body(self.backend).spec.template.spec.tolerations[0]
        self.assertEqual(toleration.key, "evalbench.io/dedicated")
        self.assertEqual(toleration.value, "eval-worker")
        self.assertEqual(toleration.effect, "NoSchedule")

    def test_does_not_retry_a_failed_eval_case(self):
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.assertEqual(_job_body(self.backend).spec.backoff_limit, 0)
        self.assertEqual(
            _job_body(self.backend).spec.template.spec.restart_policy, "Never")

    def test_sets_the_deadline_it_was_given(self):
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 1234)
        self.assertEqual(
            _job_body(self.backend).spec.active_deadline_seconds, 1234)

    def test_mounts_the_case_spec_configmap(self):
        handle = self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        pod_spec = _job_body(self.backend).spec.template.spec
        mount = next(
            m for m in pod_spec.containers[0].volume_mounts if m.name == "case-spec")
        volume = next(v for v in pod_spec.volumes if v.name == "case-spec")
        self.assertEqual(mount.mount_path, CASE_DIR)
        self.assertTrue(mount.read_only)
        self.assertEqual(volume.config_map.name, handle.name)

    def test_configmap_carries_the_serialized_spec(self):
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        data = _configmap_body(self.backend).data
        self.assertIn("config.yaml", data)
        self.assertIn("scenario.json", data)
        self.assertIn("model.0.yaml", data)

    def test_mounts_declared_secrets(self):
        self.config.secrets = [
            SecretMount(name="evalbench-sa-key", mount_path="/etc/evalbench-sa-key")
        ]
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        pod_spec = _job_body(self.backend).spec.template.spec
        volume = next(
            v for v in pod_spec.volumes if v.name == "secret-evalbench-sa-key")
        self.assertEqual(volume.secret.secret_name, "evalbench-sa-key")
        self.assertTrue(
            any(
                m.mount_path == "/etc/evalbench-sa-key"
                for m in pod_spec.containers[0].volume_mounts
            )
        )

    def _env(self):
        return {
            e.name: e.value
            for e in _job_body(self.backend).spec.template.spec.containers[0].env
        }

    def test_tells_the_container_which_pool_it_landed_on(self):
        self.backend.submit(self.spec, WorkerPool(name="pool-b"), 900)
        env = self._env()
        self.assertEqual(env["EVALBENCH_WORKER_POOL"], "pool-b")
        self.assertEqual(env["EVALBENCH_CASE_ID"], "CUJ 01")
        self.assertEqual(env["EVALBENCH_CASE_DIR"], CASE_DIR)

    def test_carries_the_orchestrator_gcp_env_into_the_case(self):
        # The sim user and LLM judges run inside the case container and resolve
        # their Vertex project/region from these; without them every case dies
        # in util.gcp.
        with patch.dict(
            os.environ,
            {"EVAL_GCP_PROJECT_ID": "proj", "EVAL_GCP_PROJECT_REGION": "us-east5"},
        ):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        env = self._env()
        self.assertEqual(env["EVAL_GCP_PROJECT_ID"], "proj")
        self.assertEqual(env["EVAL_GCP_PROJECT_REGION"], "us-east5")

    def test_explicit_env_overrides_the_inherited_value(self):
        self.config.env = {"EVAL_GCP_PROJECT_ID": "override"}
        with patch.dict(os.environ, {"EVAL_GCP_PROJECT_ID": "inherited"}):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.assertEqual(self._env()["EVAL_GCP_PROJECT_ID"], "override")

    def test_unset_inherited_vars_are_not_emitted_as_empty(self):
        with patch.dict(os.environ, {}, clear=True):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.assertNotIn("EVAL_GCP_PROJECT_ID", self._env())

    def test_inheritance_can_be_switched_off(self):
        self.config.inherit_env = []
        with patch.dict(os.environ, {"EVAL_GCP_PROJECT_ID": "proj"}):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.assertNotIn("EVAL_GCP_PROJECT_ID", self._env())

    def test_env_names_are_not_duplicated(self):
        self.config.env = {"EVALBENCH_CASE_ID": "shadow"}
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        names = [
            e.name
            for e in _job_body(self.backend).spec.template.spec.containers[0].env
        ]
        self.assertEqual(len(names), len(set(names)))

    def test_runs_as_root_like_the_eval_server(self):
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        ctx = _job_body(self.backend).spec.template.spec.security_context
        self.assertEqual(ctx.run_as_user, 0)

    def test_object_names_are_dns_1123_and_unique(self):
        names = [
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900).name
            for _ in range(3)
        ]
        self.assertEqual(len(set(names)), 3)
        for name in names:
            self.assertLessEqual(len(name), 63)
            self.assertRegex(name, r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

    def test_long_case_ids_still_produce_valid_names(self):
        spec = EvalCaseSpec.build(
            scenario={"id": "x" * 200, "starting_prompt": "hi"},
            config={"orchestrator": "agent"},
            job_id="j" * 60,
            run_time_iso="2026-09-10T12:00:00",
        )
        name = self.backend.submit(spec, WorkerPool(name="pool-a"), 900).name
        self.assertLessEqual(len(name), 63)
        self.assertRegex(name, r"^[a-z0-9]([-a-z0-9]*[a-z0-9])?$")

    def test_oversized_specs_are_rejected_before_the_api_call(self):
        self.spec.scenario["starting_prompt"] = "x" * (1024 * 1024 + 1)
        with self.assertRaisesRegex(ValueError, "ConfigMap limit"):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.backend._core.create_namespaced_config_map.assert_not_called()

    def test_a_failed_job_create_does_not_strand_its_configmap(self):
        self.backend._batch.create_namespaced_job.side_effect = RuntimeError("nope")
        with self.assertRaises(RuntimeError):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.backend._core.delete_namespaced_config_map.assert_called_once()


class TestWaitAndCollect(_BackendTestBase):

    def setUp(self):
        super().setUp()
        self.handle = CaseHandle(case_id="CUJ 01", pool="pool-a", name="ebcase-1")

    def _set_logs(self, logs):
        """Mimics the real client's raw-response contract.

        `_read_pod_log` asks for `_preload_content=False`, which returns an
        HTTP response object whose `.data` is undecoded bytes. Handing the
        double a clean `str` instead is what let the production bug through:
        the default preload path stringifies those bytes into a ``b'...'``
        repr with escaped newlines, and no test noticed.
        """
        self.backend._core.list_namespaced_pod.return_value.items = [_pod()]
        response = MagicMock()
        response.data = logs.encode("utf-8") if isinstance(logs, str) else logs
        self.backend._core.read_namespaced_pod_log.return_value = response

    def test_collects_the_payload_from_pod_logs(self):
        self.backend._batch.read_namespaced_job_status.return_value = _job_status(
            succeeded=1)
        self._set_logs(
            f"chatter\n{RESULT_BEGIN}\n"
            '{"agent_results": [{"eval_id": "CUJ 01"}], "scoring_results": [{"id": 1}]}'
            f"\n{RESULT_END}\n"
        )
        result = self.backend.wait(self.handle)

        self.assertTrue(result.ok)
        self.assertEqual(result.agent_results, [{"eval_id": "CUJ 01"}])
        self.assertEqual(result.scoring_results, [{"id": 1}])
        self.assertEqual(result.pool, "pool-a")
        self.assertEqual(result.container_ref, "ebcase-1")

    def test_a_failed_job_that_still_reported_keeps_its_payload(self):
        self.backend._batch.read_namespaced_job_status.return_value = _job_status(
            failed=1)
        self._set_logs(
            f"{RESULT_BEGIN}\n"
            '{"agent_results": [{"eval_id": "CUJ 01"}], "error": null}'
            f"\n{RESULT_END}"
        )
        result = self.backend.wait(self.handle)
        self.assertTrue(result.ok)
        self.assertEqual(result.agent_results, [{"eval_id": "CUJ 01"}])

    def test_a_job_with_no_payload_is_an_error_with_pod_detail(self):
        self.backend._batch.read_namespaced_job_status.return_value = _job_status(
            failed=1)
        pod = _pod(phase="Pending")
        condition = MagicMock()
        condition.type, condition.status = "PodScheduled", "False"
        condition.reason = "Unschedulable"
        self._set_logs("crash")
        self.backend._core.list_namespaced_pod.return_value.items = [pod]
        pod.status.conditions = [condition]

        result = self.backend.wait(self.handle)
        self.assertFalse(result.ok)
        self.assertIn("no result payload", result.error)
        self.assertIn("Unschedulable", result.error)

    def test_a_vanished_job_is_treated_as_failed_not_a_hang(self):
        from kubernetes.client.rest import ApiException

        self.backend._batch.read_namespaced_job_status.side_effect = ApiException(
            status=404)
        self._set_logs("")
        result = self.backend.wait(self.handle)
        self.assertFalse(result.ok)

    def test_client_side_timeout_reports_pod_state(self):
        self.backend._batch.read_namespaced_job_status.return_value = _job_status()
        self._set_logs("")
        self.backend._core.list_namespaced_pod.return_value.items = [
            _pod(phase="Running")]

        result = self.backend.wait(self.handle, timeout_seconds=0)
        self.assertFalse(result.ok)
        self.assertIn("did not finish", result.error)
        self.assertIn("phase=Running", result.error)

    def test_reads_logs_raw_rather_than_letting_the_client_stringify_them(self):
        """Regression: the default preload path returns `str(bytes)`.

        Asking the generated client to deserialize a log yields the *repr* of
        the bytes -- a ``b'...'`` wrapper with newlines escaped to backslash-n.
        The sentinels survive that but the JSON between them does not, so
        every case came back as "produced no result payload" against a real
        cluster while the mocked tests stayed green.
        """
        self.backend._batch.read_namespaced_job_status.return_value = _job_status(
            succeeded=1)
        self._set_logs(
            f"chatter\n{RESULT_BEGIN}\n"
            '{"agent_results": [{"eval_id": "CUJ 01"}], "scoring_results": []}'
            f"\n{RESULT_END}\n"
        )

        result = self.backend.wait(self.handle)

        self.assertTrue(result.ok, msg=result.error)
        self.assertEqual(result.agent_results, [{"eval_id": "CUJ 01"}])
        _, kwargs = self.backend._core.read_namespaced_pod_log.call_args
        self.assertFalse(
            kwargs.get("_preload_content", True),
            msg="pod logs must be read raw; the preloaded form is unparseable",
        )

    def test_the_stringified_byte_form_is_in_fact_unparseable(self):
        """Guards the reasoning above, so the fix is not 'simplified' away."""
        payload = (
            f"{RESULT_BEGIN}\n"
            '{"agent_results": [], "scoring_results": []}'
            f"\n{RESULT_END}\n"
        )
        mangled = str(payload.encode("utf-8"))
        self.assertIn(RESULT_BEGIN, mangled)
        self.assertIsNone(extract_result_payload(mangled))
        self.assertIsNotNone(extract_result_payload(payload))


class TestCleanup(_BackendTestBase):

    def setUp(self):
        super().setUp()
        self.handle = CaseHandle(case_id="c", pool="pool-a", name="ebcase-1")

    def test_deletes_job_and_configmap_on_success(self):
        self.backend.cleanup(self.handle, succeeded=True)
        self.backend._batch.delete_namespaced_job.assert_called_once()
        self.backend._core.delete_namespaced_config_map.assert_called_once()

    def test_keeps_a_failed_job_for_inspection_by_default(self):
        self.backend.cleanup(self.handle, succeeded=False)
        self.backend._batch.delete_namespaced_job.assert_not_called()

    def test_deletes_a_failed_job_when_configured_to(self):
        self.config.keep_failed_jobs = False
        self.backend.cleanup(self.handle, succeeded=False)
        self.backend._batch.delete_namespaced_job.assert_called_once()

    def test_a_missing_object_does_not_raise(self):
        from kubernetes.client.rest import ApiException

        self.backend._batch.delete_namespaced_job.side_effect = ApiException(
            status=404)
        self.backend._core.delete_namespaced_config_map.side_effect = ApiException(
            status=404)
        self.backend.cleanup(self.handle, succeeded=True)


if __name__ == "__main__":
    unittest.main()
