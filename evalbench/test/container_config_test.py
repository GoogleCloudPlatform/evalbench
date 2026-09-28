import threading
import unittest

from container.config import (
    DEFAULT_CASE_TIMEOUT_SECONDS,
    DEFAULT_EPHEMERAL_STORAGE_LIMIT,
    DEFAULT_EPHEMERAL_STORAGE_REQUEST,
    DEFAULT_POOL_LABEL,
    DEFAULT_TIMEOUT_GRACE_SECONDS,
    ContainerizationConfig,
    WorkerPool,
    load_containerization_config,
)
from container.pool_router import (
    WorkerPoolRouter,
    reset_shared_slots,
    shared_slots,
)


def _raw(**overrides):
    base = {
        "enabled": True,
        "image": "img:latest",
        "worker_pools": ["pool-a", "pool-b"],
    }
    base.update(overrides)
    return {"containerization": base}


class TestLoadContainerizationConfig(unittest.TestCase):

    def test_absent_block_is_disabled(self):
        cfg = load_containerization_config({})
        self.assertFalse(cfg.enabled)

    def test_explicitly_disabled_block_needs_no_other_fields(self):
        cfg = load_containerization_config({"containerization": {"enabled": False}})
        self.assertFalse(cfg.enabled)

    def test_non_mapping_block_is_rejected(self):
        with self.assertRaises(ValueError):
            load_containerization_config({"containerization": ["enabled"]})

    def test_enabled_without_image_is_rejected(self):
        raw = _raw()
        del raw["containerization"]["image"]
        with self.assertRaisesRegex(ValueError, "image"):
            load_containerization_config(raw)

    def test_enabled_without_worker_pools_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "worker_pools"):
            load_containerization_config(_raw(worker_pools=[]))

    def test_duplicate_pool_names_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            load_containerization_config(_raw(worker_pools=["a", "a"]))

    def test_defaults(self):
        cfg = load_containerization_config(_raw())
        self.assertEqual(cfg.backend, "gke")
        self.assertEqual(cfg.namespace, "evalbench-namespace")
        self.assertEqual(cfg.service_account, "evalbench-ksa")
        self.assertEqual(cfg.pool_selector_label, DEFAULT_POOL_LABEL)
        self.assertEqual(cfg.pool_names, ["pool-a", "pool-b"])

    def test_pool_entries_may_be_mappings_with_overrides(self):
        cfg = load_containerization_config(
            _raw(
                worker_pools=[
                    "pool-a",
                    {
                        "name": "pool-b",
                        "max_concurrent": 2,
                        "node_selector": {"disktype": "ssd"},
                    },
                ],
                max_concurrent_per_pool=5,
            )
        )
        pool_a, pool_b = cfg.worker_pools
        self.assertEqual(cfg.pool_capacity(pool_a), 5)
        self.assertEqual(cfg.pool_capacity(pool_b), 2)
        self.assertEqual(pool_b.node_selector, {"disktype": "ssd"})
        self.assertEqual(cfg.total_capacity, 7)

    def test_pool_entry_without_name_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "name"):
            load_containerization_config(_raw(worker_pools=[{"max_concurrent": 1}]))

    def test_secrets_are_parsed(self):
        cfg = load_containerization_config(
            _raw(secrets=[{"name": "sa-key", "mount_path": "/etc/key", "optional": True}])
        )
        self.assertEqual(cfg.secrets[0].name, "sa-key")
        self.assertEqual(cfg.secrets[0].mount_path, "/etc/key")
        self.assertTrue(cfg.secrets[0].optional)

    def test_secret_without_mount_path_is_rejected(self):
        with self.assertRaisesRegex(ValueError, "mount_path"):
            load_containerization_config(_raw(secrets=[{"name": "sa-key"}]))

    def test_job_timeout_accepts_duration_strings(self):
        cfg = load_containerization_config(_raw(job_timeout="45m"))
        self.assertEqual(cfg.deadline_seconds(60), 45 * 60)


class TestDeadlineSeconds(unittest.TestCase):

    def test_derives_from_case_timeout_plus_grace(self):
        cfg = ContainerizationConfig(enabled=True)
        self.assertEqual(
            cfg.deadline_seconds(120), int(120 + DEFAULT_TIMEOUT_GRACE_SECONDS))

    def test_falls_back_when_no_case_timeout(self):
        cfg = ContainerizationConfig(enabled=True)
        self.assertEqual(
            cfg.deadline_seconds(None),
            int(DEFAULT_CASE_TIMEOUT_SECONDS + DEFAULT_TIMEOUT_GRACE_SECONDS),
        )

    def test_explicit_job_timeout_wins(self):
        cfg = ContainerizationConfig(enabled=True, job_timeout_seconds=99)
        self.assertEqual(cfg.deadline_seconds(100000), 99)


class TestWorkerPoolRouter(unittest.TestCase):

    def _router(self, names, per_pool=1):
        cfg = ContainerizationConfig(
            enabled=True,
            worker_pools=[WorkerPool(name=n) for n in names],
            max_concurrent_per_pool=per_pool,
        )
        return WorkerPoolRouter(cfg)

    def test_requires_at_least_one_pool(self):
        with self.assertRaises(ValueError):
            WorkerPoolRouter(ContainerizationConfig(enabled=True))

    def test_cycles_pools_round_robin(self):
        router = self._router(["a", "b", "c"], per_pool=4)
        picked = []
        for _ in range(6):
            with router.acquire() as pool:
                picked.append(pool.name)
        self.assertEqual(picked, ["a", "b", "c", "a", "b", "c"])

    def test_skips_a_saturated_pool_instead_of_blocking(self):
        router = self._router(["a", "b"], per_pool=1)
        with router.acquire() as first:
            self.assertEqual(first.name, "a")
            # Cursor now points at "b", which is free.
            with router.acquire() as second:
                self.assertEqual(second.name, "b")

    def test_releases_the_slot_on_exit(self):
        router = self._router(["a"], per_pool=1)
        for _ in range(3):
            with router.acquire() as pool:
                self.assertEqual(pool.name, "a")

    def test_releases_the_slot_when_the_body_raises(self):
        router = self._router(["a"], per_pool=1)
        with self.assertRaises(RuntimeError):
            with router.acquire():
                raise RuntimeError("boom")
        with router.acquire() as pool:
            self.assertEqual(pool.name, "a")

    def test_never_exceeds_total_capacity(self):
        router = self._router(["a", "b"], per_pool=2)
        self.assertEqual(router.capacity, 4)

        peak = 0
        current = 0
        lock = threading.Lock()
        start = threading.Barrier(8, timeout=10)
        errors = []

        def worker():
            nonlocal peak, current
            try:
                start.wait()
                with router.acquire():
                    with lock:
                        current += 1
                        peak = max(peak, current)
                    with lock:
                        current -= 1
            except Exception as e:  # surfaced after join
                errors.append(e)

        threads = [threading.Thread(target=worker) for _ in range(8)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=10)

        self.assertEqual(errors, [])
        self.assertLessEqual(peak, 4)

    def test_in_flight_is_tracked_per_pool(self):
        router = self._router(["a", "b"], per_pool=2)
        with router.acquire() as pool:
            self.assertEqual(router.in_flight()[pool.name], 1)
        self.assertEqual(sum(router.in_flight().values()), 0)

    def test_rejects_slots_that_miss_a_pool(self):
        cfg = ContainerizationConfig(
            enabled=True,
            worker_pools=[WorkerPool(name="a"), WorkerPool(name="b")],
        )
        with self.assertRaises(ValueError):
            WorkerPoolRouter(cfg, slots={"a": threading.Semaphore(1)})


class TestSharedSlots(unittest.TestCase):
    """Capacity has to bound the cluster, not one eval session's view of it."""

    def setUp(self):
        reset_shared_slots()
        self.addCleanup(reset_shared_slots)

    def _config(self, names, per_pool=1, namespace="ns"):
        return ContainerizationConfig(
            enabled=True,
            namespace=namespace,
            worker_pools=[WorkerPool(name=n) for n in names],
            max_concurrent_per_pool=per_pool,
        )

    def test_two_sessions_share_one_budget(self):
        cfg = self._config(["a"], per_pool=1)
        first = WorkerPoolRouter(cfg, slots=shared_slots(cfg))
        second = WorkerPoolRouter(cfg, slots=shared_slots(cfg))

        acquired_by_second = threading.Event()

        def take_second():
            with second.acquire():
                acquired_by_second.set()

        with first.acquire():
            thread = threading.Thread(target=take_second)
            thread.start()
            # The single slot is held by `first`, so the second session must
            # block rather than dispatching a case of its own.
            self.assertFalse(acquired_by_second.wait(timeout=0.5))

        thread.join(timeout=5)
        self.assertTrue(acquired_by_second.is_set())

    def test_private_slots_do_not_share(self):
        cfg = self._config(["a"], per_pool=1)
        first = WorkerPoolRouter(cfg)
        second = WorkerPoolRouter(cfg)
        # Without the shared registry each router has its own semaphore, which
        # is exactly the over-dispatch the shared one exists to prevent.
        with first.acquire(), second.acquire():
            pass

    def test_namespaces_get_separate_budgets(self):
        one = self._config(["a"], per_pool=1, namespace="ns-1")
        two = self._config(["a"], per_pool=1, namespace="ns-2")
        router_one = WorkerPoolRouter(one, slots=shared_slots(one))
        router_two = WorkerPoolRouter(two, slots=shared_slots(two))
        with router_one.acquire(), router_two.acquire():
            pass

    def test_first_capacity_wins_and_warns(self):
        first = self._config(["a"], per_pool=1)
        shared_slots(first)
        second = self._config(["a"], per_pool=8)
        with self.assertLogs(level="WARNING") as logs:
            slots = shared_slots(second)
        self.assertIn("already capped at 1", "\n".join(logs.output))
        # Same semaphore object, so the original limit still applies.
        self.assertTrue(slots["a"].acquire(blocking=False))
        self.assertFalse(slots["a"].acquire(blocking=False))
        slots["a"].release()


class TestEphemeralStorageDefaults(unittest.TestCase):
    """A case must always declare disk, or it can evict its neighbours.

    Regression cover for a 100-case run that filled a node's ephemeral
    storage and got the eval server evicted: nothing requested disk, so the
    scheduler happily packed pods until the kubelet started shedding load.
    """

    def _pool(self, resources=None):
        return WorkerPool(name="a", resources=resources)

    def test_a_bare_config_still_requests_disk(self):
        config = ContainerizationConfig(worker_pools=[self._pool()])
        resources = config.pool_resources(config.worker_pools[0])
        self.assertEqual(
            resources["requests"]["ephemeral-storage"],
            DEFAULT_EPHEMERAL_STORAGE_REQUEST)
        self.assertEqual(
            resources["limits"]["ephemeral-storage"],
            DEFAULT_EPHEMERAL_STORAGE_LIMIT)

    def test_cpu_and_memory_survive_the_injection(self):
        config = ContainerizationConfig(
            worker_pools=[self._pool()],
            resources={
                "requests": {"cpu": "2", "memory": "8Gi"},
                "limits": {"cpu": "4", "memory": "16Gi"},
            },
        )
        resources = config.pool_resources(config.worker_pools[0])
        self.assertEqual(resources["requests"]["cpu"], "2")
        self.assertEqual(resources["requests"]["memory"], "8Gi")
        self.assertEqual(resources["limits"]["cpu"], "4")
        self.assertIn("ephemeral-storage", resources["requests"])

    def test_an_explicit_value_is_left_alone(self):
        config = ContainerizationConfig(
            worker_pools=[self._pool()],
            resources={"requests": {"ephemeral-storage": "64Gi"}},
        )
        resources = config.pool_resources(config.worker_pools[0])
        self.assertEqual(resources["requests"]["ephemeral-storage"], "64Gi")

    def test_the_caller_config_is_not_mutated(self):
        shared = {"requests": {"cpu": "1"}}
        config = ContainerizationConfig(
            worker_pools=[self._pool()], resources=shared)
        config.pool_resources(config.worker_pools[0])
        self.assertNotIn("ephemeral-storage", shared["requests"])
        self.assertNotIn("limits", shared)

    def test_pool_overrides_also_get_a_default(self):
        config = ContainerizationConfig(
            worker_pools=[self._pool(resources={"requests": {"cpu": "3"}})],
            resources={"requests": {"ephemeral-storage": "99Gi"}},
        )
        resources = config.pool_resources(config.worker_pools[0])
        # The pool override replaces the global block wholesale, so the
        # default has to be applied to it too rather than inherited.
        self.assertEqual(resources["requests"]["cpu"], "3")
        self.assertEqual(
            resources["requests"]["ephemeral-storage"],
            DEFAULT_EPHEMERAL_STORAGE_REQUEST)


if __name__ == "__main__":
    unittest.main()
