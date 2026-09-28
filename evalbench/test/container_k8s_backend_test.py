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


def _make_backend(config, clock=None, environ=None):
    """Builds a KubernetesJobBackend with its API clients mocked out."""
    from container.k8s_backend import KubernetesJobBackend

    kwargs = {"sleep": MagicMock()}
    if clock is not None:
        kwargs["clock"] = clock
    with patch("kubernetes.config.load_incluster_config"), patch(
        "kubernetes.client.BatchV1Api"
    ) as batch, patch("kubernetes.client.CoreV1Api") as core, patch.dict(
        os.environ, environ or {}
    ):
        backend = KubernetesJobBackend(config, **kwargs)
    backend._batch = batch.return_value
    backend._core = core.return_value
    _set_jobs(backend)
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


def _job(name, **status):
    job = _job_status(**status)
    job.metadata.name = name
    return job


def _page(*jobs, token=None):
    page = MagicMock()
    page.items = list(jobs)
    page.metadata._continue = token
    return page


def _set_jobs(backend, *jobs):
    """What the shared status LIST returns."""
    backend._batch.list_namespaced_job.return_value = _page(*jobs)
    backend._batch.list_namespaced_job.side_effect = None


def _api_error(status, headers=None):
    from kubernetes.client.rest import ApiException

    error = ApiException(status=status, reason=f"HTTP {status}")
    error.headers = headers
    return error


class _FakeClock:

    def __init__(self, now=1000.0):
        self.now = now

    def __call__(self):
        return self.now


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
        _set_jobs(self.backend, _job("ebcase-1", succeeded=1))
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
        _set_jobs(self.backend, _job("ebcase-1", failed=1))
        self._set_logs(
            f"{RESULT_BEGIN}\n"
            '{"agent_results": [{"eval_id": "CUJ 01"}], "error": null}'
            f"\n{RESULT_END}"
        )
        result = self.backend.wait(self.handle)
        self.assertTrue(result.ok)
        self.assertEqual(result.agent_results, [{"eval_id": "CUJ 01"}])

    def test_a_job_with_no_payload_is_an_error_with_pod_detail(self):
        _set_jobs(self.backend, _job("ebcase-1", failed=1))
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
        _set_jobs(self.backend, _job("ebcase-1"))
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
        _set_jobs(self.backend, _job("ebcase-1", succeeded=1))
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


class TestExtractResultPayload(unittest.TestCase):
    """Pod logs merge stdout and stderr, which can reorder."""

    def test_skips_a_stderr_line_between_the_markers(self):
        # Shape of two case logs from the 100-run GKE load test that the old
        # parser reported as "produced no result payload".
        logs = (
            "harness chatter\n"
            f"\n{RESULT_BEGIN}\n"
            "2026-09-28 13:23:50,996 INFO Uploaded /evalbench/.venv/fake_home"
            " to gs://bucket/nightly_evals/job/case.zip\n"
            '{"case_id": "c", "agent_results": [{"eval_id": "c"}]}\n'
            f"{RESULT_END}\n"
        )
        payload = extract_result_payload(logs)
        self.assertIsNotNone(payload)
        self.assertEqual(payload["agent_results"], [{"eval_id": "c"}])

    def test_skips_stray_lines_on_both_sides_of_the_payload(self):
        logs = (
            f"{RESULT_BEGIN}\nWARNING before\n{{not json\n"
            '{"case_id": "c"}\n'
            f"INFO after\n{RESULT_END}\n"
        )
        self.assertEqual(extract_result_payload(logs), {"case_id": "c"})

    def test_a_non_object_line_is_not_a_payload(self):
        logs = f"{RESULT_BEGIN}\n[1, 2]\n{RESULT_END}\n"
        self.assertIsNone(extract_result_payload(logs))

    def test_still_none_when_there_is_no_json(self):
        logs = f"{RESULT_BEGIN}\nINFO only a log line\n{RESULT_END}\n"
        self.assertIsNone(extract_result_payload(logs))


class TestSharedStatusList(_BackendTestBase):
    """Many waiters, one LIST per poll interval."""

    def setUp(self):
        super().setUp()
        self.clock = _FakeClock()
        self.config.poll_interval_seconds = 10
        self.backend = _make_backend(self.config, clock=self.clock)

    def _submit(self):
        return self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)

    def test_one_list_serves_every_waiter_within_an_interval(self):
        handles = [self._submit() for _ in range(3)]
        self.clock.now += 1
        _set_jobs(self.backend, *[_job(h.name) for h in handles])

        for handle in handles:
            self.assertEqual(self.backend._job_status(handle), "running")

        self.backend._batch.list_namespaced_job.assert_called_once()
        self.backend._batch.read_namespaced_job_status.assert_not_called()

    def test_the_list_is_refreshed_once_the_interval_passes(self):
        handle = self._submit()
        self.clock.now += 1
        _set_jobs(self.backend, _job(handle.name))
        self.backend._job_status(handle)

        self.clock.now += 10
        _set_jobs(self.backend, _job(handle.name, succeeded=1))
        self.assertEqual(self.backend._job_status(handle), "succeeded")
        self.assertEqual(self.backend._batch.list_namespaced_job.call_count, 2)

    def test_selects_only_this_dispatchers_eval_case_jobs(self):
        self.backend._job_status(self._submit())
        kwargs = self.backend._batch.list_namespaced_job.call_args.kwargs
        self.assertEqual(
            kwargs["label_selector"],
            f"app=evalbench-eval-case,evalbench.io/dispatcher="
            f"{self.backend.dispatcher}",
        )
        self.assertEqual(kwargs["namespace"], "evalbench-namespace")

    def test_a_job_newer_than_the_snapshot_is_running_without_a_get(self):
        # Snapshot taken first, then a new Job lands before the next refresh.
        self.backend._job_status(self._submit())
        self.clock.now += 1
        late = self._submit()

        self.assertEqual(self.backend._job_status(late), "running")
        self.backend._batch.read_namespaced_job_status.assert_not_called()

    def test_a_job_missing_from_a_newer_snapshot_is_confirmed_by_get(self):
        handle = self._submit()
        self.clock.now += 1
        _set_jobs(self.backend)  # reaped between polls
        self.backend._batch.read_namespaced_job_status.side_effect = _api_error(404)

        self.assertEqual(self.backend._job_status(handle), "failed")
        self.backend._batch.read_namespaced_job_status.assert_called_once()

    def test_a_failed_list_falls_back_to_per_job_reads(self):
        handle = self._submit()
        self.clock.now += 1
        self.backend._batch.list_namespaced_job.side_effect = _api_error(403)
        self.backend._batch.read_namespaced_job_status.return_value = _job_status(
            succeeded=1)

        self.assertEqual(self.backend._job_status(handle), "succeeded")

    def test_follows_pagination(self):
        handle = self._submit()
        self.clock.now += 1
        self.backend._batch.list_namespaced_job.side_effect = [
            _page(_job("other"), token="page-2"),
            _page(_job(handle.name, failed=1)),
        ]

        self.assertEqual(self.backend._job_status(handle), "failed")
        calls = self.backend._batch.list_namespaced_job.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[1].kwargs["_continue"], "page-2")

    def test_concurrent_waiters_share_one_in_flight_list(self):
        import threading

        handle = self._submit()
        self.clock.now += 1
        release = threading.Event()
        started = threading.Event()

        def slow_list(**_):
            started.set()
            release.wait(timeout=5)
            return _page(_job(handle.name))

        self.backend._batch.list_namespaced_job.side_effect = slow_list
        results = []
        threads = [
            threading.Thread(
                target=lambda: results.append(self.backend._job_status(handle)))
            for _ in range(5)
        ]
        for t in threads:
            t.start()
        started.wait(timeout=5)
        release.set()
        for t in threads:
            t.join(timeout=5)

        self.assertEqual(results, ["running"] * 5)
        self.backend._batch.list_namespaced_job.assert_called_once()


class TestOwnership(_BackendTestBase):

    _ENV = {
        "EVALBENCH_POD_NAME": "evalbench-7f9c-abcde",
        "EVALBENCH_POD_UID": "pod-uid-1",
        "EVALBENCH_POD_NAMESPACE": "evalbench-namespace",
    }

    def test_owner_is_the_eval_server_pod_in_the_same_namespace(self):
        from container.k8s_backend import owner_pod

        self.assertEqual(
            owner_pod("evalbench-namespace", self._ENV),
            ("evalbench-7f9c-abcde", "pod-uid-1"))

    def test_no_owner_across_namespaces(self):
        # A cross-namespace ownerReference makes the GC delete the Job at once.
        from container.k8s_backend import owner_pod

        self.assertIsNone(owner_pod("evalbench-test-namespace", self._ENV))

    def test_no_owner_outside_the_cluster(self):
        from container.k8s_backend import owner_pod

        self.assertIsNone(owner_pod("evalbench-namespace", {}))

    def test_namespace_falls_back_to_the_service_account_mount(self):
        from container.k8s_backend import owner_pod

        ns_file = os.path.join(self.tmp, "namespace")
        with open(ns_file, "w") as f:
            f.write("evalbench-namespace\n")
        env = {k: v for k, v in self._ENV.items() if k != "EVALBENCH_POD_NAMESPACE"}
        self.assertEqual(
            owner_pod("evalbench-namespace", env, namespace_file=ns_file),
            ("evalbench-7f9c-abcde", "pod-uid-1"))

    def test_dispatcher_id_is_the_pod_name(self):
        from container.k8s_backend import dispatcher_id

        self.assertEqual(dispatcher_id(self._ENV), "evalbench-7f9c-abcde")

    def test_job_is_owned_by_the_eval_server_pod(self):
        backend = _make_backend(self.config, environ=self._ENV)
        backend.submit(self.spec, WorkerPool(name="pool-a"), 900)

        refs = _job_body(backend).metadata.owner_references
        self.assertEqual(len(refs), 1)
        self.assertEqual((refs[0].kind, refs[0].name, refs[0].uid),
                         ("Pod", "evalbench-7f9c-abcde", "pod-uid-1"))
        self.assertFalse(refs[0].block_owner_deletion)
        self.assertFalse(refs[0].controller)

    def test_every_object_carries_the_dispatcher_label(self):
        backend = _make_backend(self.config, environ=self._ENV)
        backend.submit(self.spec, WorkerPool(name="pool-a"), 900)

        job = _job_body(backend)
        for labels in (
            job.metadata.labels,
            job.spec.template.metadata.labels,
            _configmap_body(backend).metadata.labels,
        ):
            self.assertEqual(
                labels["evalbench.io/dispatcher"], "evalbench-7f9c-abcde")

    def test_configmap_is_adopted_by_its_job(self):
        self.backend._batch.create_namespaced_job.return_value.metadata.uid = (
            "job-uid-1")
        handle = self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)

        kwargs = self.backend._core.patch_namespaced_config_map.call_args.kwargs
        self.assertEqual(kwargs["name"], handle.name)
        (ref,) = kwargs["body"]["metadata"]["ownerReferences"]
        self.assertEqual(ref["kind"], "Job")
        self.assertEqual(ref["name"], handle.name)
        self.assertEqual(ref["uid"], "job-uid-1")

    def test_adoption_failure_does_not_fail_the_case(self):
        self.backend._batch.create_namespaced_job.return_value.metadata.uid = (
            "job-uid-1")
        self.backend._core.patch_namespaced_config_map.side_effect = _api_error(403)

        handle = self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)

        self.assertTrue(handle.name)
        self.backend._core.delete_namespaced_config_map.assert_not_called()


class TestRetries(_BackendTestBase):

    def test_transient_errors_are_retried(self):
        self.backend._batch.create_namespaced_job.side_effect = [
            _api_error(503), _api_error(429), MagicMock()]
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)

        self.assertEqual(self.backend._batch.create_namespaced_job.call_count, 3)
        self.assertEqual(self.backend._sleep.call_count, 2)

    def test_connection_errors_are_retried(self):
        import urllib3

        self.backend._core.create_namespaced_config_map.side_effect = [
            urllib3.exceptions.ProtocolError("reset"), MagicMock()]
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.assertEqual(
            self.backend._core.create_namespaced_config_map.call_count, 2)

    def test_conflict_on_a_retry_means_the_first_attempt_landed(self):
        existing = MagicMock()
        existing.metadata.uid = "job-uid-1"
        self.backend._batch.create_namespaced_job.side_effect = [
            _api_error(504), _api_error(409)]
        self.backend._batch.read_namespaced_job.return_value = existing

        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)

        body = self.backend._core.patch_namespaced_config_map.call_args.kwargs["body"]
        self.assertEqual(
            body["metadata"]["ownerReferences"][0]["uid"], "job-uid-1")
        self.backend._core.delete_namespaced_config_map.assert_not_called()

    def test_conflict_on_the_first_attempt_is_a_real_collision(self):
        self.backend._batch.create_namespaced_job.side_effect = _api_error(409)
        with self.assertRaises(Exception):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.backend._core.delete_namespaced_config_map.assert_called_once()

    def test_client_errors_are_not_retried(self):
        self.backend._batch.create_namespaced_job.side_effect = _api_error(403)
        with self.assertRaises(Exception):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.backend._batch.create_namespaced_job.assert_called_once()
        self.backend._sleep.assert_not_called()

    def test_gives_up_after_a_bounded_number_of_attempts(self):
        from container.k8s_backend import _MAX_ATTEMPTS

        self.backend._batch.create_namespaced_job.side_effect = _api_error(503)
        with self.assertRaises(Exception):
            self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)
        self.assertEqual(
            self.backend._batch.create_namespaced_job.call_count, _MAX_ATTEMPTS)

    def test_backoff_honours_retry_after_but_stays_bounded(self):
        from container.k8s_backend import _BACKOFF_MAX_SECONDS

        self.backend._batch.create_namespaced_job.side_effect = [
            _api_error(429, headers={"Retry-After": "3"}),
            _api_error(429, headers={"Retry-After": "600"}),
            MagicMock(),
        ]
        self.backend.submit(self.spec, WorkerPool(name="pool-a"), 900)

        delays = [c.args[0] for c in self.backend._sleep.call_args_list]
        self.assertGreaterEqual(delays[0], 3)
        self.assertEqual(delays[1], _BACKOFF_MAX_SECONDS)


class TestLogs(_BackendTestBase):

    def setUp(self):
        super().setUp()
        self.handle = CaseHandle(case_id="CUJ 01", pool="pool-a", name="ebcase-1")
        _set_jobs(self.backend, _job("ebcase-1", succeeded=1))
        self.backend._core.list_namespaced_pod.return_value.items = [_pod()]

    def _respond(self, *logs):
        responses = []
        for text in logs:
            response = MagicMock()
            response.data = text.encode("utf-8")
            responses.append(response)
        self.backend._core.read_namespaced_pod_log.side_effect = responses

    _PAYLOAD = (
        f"{RESULT_BEGIN}\n"
        '{"agent_results": [{"eval_id": "CUJ 01"}]}'
        f"\n{RESULT_END}\n"
    )

    def test_reads_only_the_tail_when_it_holds_the_payload(self):
        self._respond("chatter\n" + self._PAYLOAD)
        result = self.backend.wait(self.handle)

        self.assertTrue(result.ok, msg=result.error)
        self.backend._core.read_namespaced_pod_log.assert_called_once()
        kwargs = self.backend._core.read_namespaced_pod_log.call_args.kwargs
        self.assertEqual(kwargs["tail_lines"], 200)

    def test_falls_back_to_the_full_log_when_the_tail_misses(self):
        self._respond("trailing noise\n", self._PAYLOAD + "trailing noise\n")
        result = self.backend.wait(self.handle)

        self.assertTrue(result.ok, msg=result.error)
        calls = self.backend._core.read_namespaced_pod_log.call_args_list
        self.assertEqual(len(calls), 2)
        self.assertNotIn("tail_lines", calls[1].kwargs)

    def test_result_keeps_only_the_end_of_a_huge_log(self):
        from container.k8s_backend import _MAX_RESULT_LOG_CHARS

        big = "x" * (_MAX_RESULT_LOG_CHARS * 3) + "\n" + self._PAYLOAD
        self._respond(big)
        result = self.backend.wait(self.handle)

        self.assertTrue(result.ok, msg=result.error)
        self.assertLess(len(result.logs), _MAX_RESULT_LOG_CHARS + 100)
        self.assertTrue(result.logs.rstrip().endswith(RESULT_END))
        self.assertIn("truncated", result.logs)


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
