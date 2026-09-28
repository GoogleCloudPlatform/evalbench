import json
import os
import shutil
import tempfile
import unittest

import yaml

from container.spec import CASE_DIR, CONTAINER_WORK_DIR_ROOT, EvalCaseSpec, sanitize_case_id


class TestSanitizeCaseId(unittest.TestCase):

    def test_lowercases_and_replaces_unsafe_chars(self):
        self.assertEqual(
            sanitize_case_id("List Instances/CUJ_01"), "list-instances-cuj-01")

    def test_strips_leading_and_trailing_separators(self):
        self.assertEqual(sanitize_case_id("__abc__"), "abc")

    def test_falls_back_when_nothing_survives(self):
        self.assertEqual(sanitize_case_id("!!!"), "case")

    def test_truncates_to_max_len(self):
        self.assertEqual(len(sanitize_case_id("a" * 100, max_len=12)), 12)


class TestEvalCaseSpecBuild(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.model_path = os.path.join(self.tmp, "claude_model.yaml")
        with open(self.model_path, "w") as f:
            yaml.safe_dump({"generator": "claude_code", "model": "opus"}, f)

        self.judge_path = os.path.join(self.tmp, "judge_model.yaml")
        with open(self.judge_path, "w") as f:
            yaml.safe_dump({"generator": "gcp_vertex_gemini"}, f)

        self.config = {
            "orchestrator": "agent",
            "dataset_config": "datasets/foo.json",
            "model_config": self.model_path,
            "simulated_user_model_config": self.judge_path,
            "set_up_script": "setup.sh",
            "tear_down_script": "teardown.sh",
            "runners": {"agent_runners": 10},
            "containerization": {"enabled": True, "worker_pools": ["p1"]},
            "scorers": {
                "goal_completion": {"model_config": self.judge_path},
                "turn_count": {},
            },
        }
        self.scenario = {"id": "cuj_01", "starting_prompt": "hello"}

    def _build(self, **kwargs):
        return EvalCaseSpec.build(
            scenario=kwargs.pop("scenario", self.scenario),
            config=kwargs.pop("config", self.config),
            job_id=kwargs.pop("job_id", "job-abc"),
            run_time_iso=kwargs.pop("run_time_iso", "2026-09-10T00:00:00"),
            **kwargs,
        )

    def test_inlines_every_referenced_model_config(self):
        spec = self._build()
        # Two distinct files: the agent model and the judge model (referenced
        # twice, inlined once).
        self.assertEqual(len(spec.model_files), 2)
        self.assertIn(
            "generator: claude_code", "".join(spec.model_files.values()))

    def test_rewrites_model_paths_to_container_paths(self):
        spec = self._build()
        self.assertEqual(
            os.path.dirname(spec.config["model_config"]), CASE_DIR)
        self.assertEqual(
            os.path.dirname(
                spec.config["scorers"]["goal_completion"]["model_config"]),
            CASE_DIR,
        )

    def test_deduplicates_a_path_referenced_twice(self):
        spec = self._build()
        self.assertEqual(
            spec.config["simulated_user_model_config"],
            spec.config["scorers"]["goal_completion"]["model_config"],
        )

    def test_strips_recursion_and_run_level_scripts(self):
        spec = self._build()
        self.assertNotIn("containerization", spec.config)
        self.assertNotIn("set_up_script", spec.config)
        self.assertNotIn("tear_down_script", spec.config)
        self.assertNotIn("dataset_config", spec.config)

    def test_forces_single_runner_in_the_container(self):
        spec = self._build()
        self.assertEqual(spec.config["runners"]["agent_runners"], 1)

    def test_does_not_mutate_the_caller_config(self):
        self._build()
        self.assertEqual(self.config["model_config"], self.model_path)
        self.assertIn("containerization", self.config)
        self.assertEqual(self.config["runners"]["agent_runners"], 10)

    def test_leaves_unreadable_model_path_untouched(self):
        config = dict(self.config, model_config="/nope/missing.yaml")
        spec = self._build(config=config)
        self.assertEqual(spec.config["model_config"], "/nope/missing.yaml")

    def test_reroots_work_dir_into_the_container(self):
        scenario = dict(self.scenario, resolved_work_dir=self.tmp)
        spec = self._build(scenario=scenario)
        self.assertEqual(
            spec.scenario["resolved_work_dir"],
            os.path.join(CONTAINER_WORK_DIR_ROOT, "cuj-01"),
        )

    def test_collects_declared_env_files(self):
        env_dir = os.path.join(self.tmp, "session", "env_files", "env")
        os.makedirs(env_dir)
        with open(os.path.join(env_dir, "sleep.py"), "w") as f:
            f.write("print('hi')")

        scenario = dict(self.scenario, env_files=["env/sleep.py"])
        spec = self._build(
            scenario=scenario, session_dir=os.path.join(self.tmp, "session"))
        self.assertEqual(spec.env_files, {"env/sleep.py": "print('hi')"})

    def test_missing_env_file_is_skipped_not_fatal(self):
        scenario = dict(self.scenario, env_files=["env/absent.py"])
        spec = self._build(
            scenario=scenario, session_dir=os.path.join(self.tmp, "session"))
        self.assertEqual(spec.env_files, {})


class TestEvalCaseSpecRoundTrip(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.model_path = os.path.join(self.tmp, "model.yaml")
        with open(self.model_path, "w") as f:
            yaml.safe_dump({"generator": "claude_code"}, f)

        self.spec = EvalCaseSpec.build(
            scenario={
                "id": "cuj/02",
                "starting_prompt": "hi",
                "env_files": ["env/a.txt"],
            },
            config={"model_config": self.model_path, "orchestrator": "agent"},
            job_id="job-1",
            run_time_iso="2026-09-10T12:00:00",
        )
        self.spec.env_files = {"env/a.txt": "contents"}

    def test_file_keys_are_configmap_safe(self):
        for key in self.spec.to_files():
            self.assertRegex(key, r"^[-._a-zA-Z0-9]+$", msg=key)

    def test_write_then_load_preserves_the_case(self):
        case_dir = os.path.join(self.tmp, "case")
        self.spec.write_to_dir(case_dir)
        loaded = EvalCaseSpec.load_from_dir(case_dir)

        self.assertEqual(loaded.case_id, "cuj/02")
        self.assertEqual(loaded.job_id, "job-1")
        self.assertEqual(loaded.run_time_iso, "2026-09-10T12:00:00")
        self.assertEqual(loaded.scenario["starting_prompt"], "hi")
        self.assertEqual(loaded.env_files, {"env/a.txt": "contents"})
        self.assertEqual(loaded.model_files, self.spec.model_files)

    def test_resolve_model_paths_reanchors_to_the_mount_point(self):
        case_dir = os.path.join(self.tmp, "case")
        self.spec.write_to_dir(case_dir)
        loaded = EvalCaseSpec.load_from_dir(case_dir)

        config = loaded.resolve_model_paths(case_dir)
        self.assertEqual(os.path.dirname(config["model_config"]), case_dir)
        self.assertTrue(os.path.isfile(config["model_config"]))

    def test_resolve_model_paths_is_a_noop_at_the_real_mount_point(self):
        config = self.spec.resolve_model_paths(CASE_DIR)
        self.assertEqual(config["model_config"], self.spec.config["model_config"])

    def test_size_bytes_counts_every_file(self):
        expected = sum(
            len(v.encode("utf-8")) for v in self.spec.to_files().values())
        self.assertEqual(self.spec.size_bytes, expected)

    def test_config_file_is_valid_yaml_and_scenario_valid_json(self):
        files = self.spec.to_files()
        self.assertEqual(
            yaml.safe_load(files["config.yaml"])["orchestrator"], "agent")
        self.assertEqual(json.loads(files["scenario.json"])["id"], "cuj/02")


if __name__ == "__main__":
    unittest.main()
