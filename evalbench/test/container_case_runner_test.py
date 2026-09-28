"""End-to-end tests for the in-container entrypoint.

`case_runner.py` is what the eval-case container actually executes, so these
run the real script as a subprocess: they cover the sentinel discipline (result
on stdout, logs on stderr) that the orchestrator depends on to get results back
out of pod logs. The `noop_agent` generator stands in for Claude Code so the
whole path -- spec load, AgentEvaluator, scoring, payload emit -- runs without
launching a CLI or calling a model.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest

import yaml

from container.backend import RESULT_BEGIN, RESULT_END, extract_result_payload
from container.spec import EvalCaseSpec

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
_PACKAGE_ROOT = os.path.join(_REPO_ROOT, "evalbench")
_CASE_RUNNER = os.path.join(_PACKAGE_ROOT, "container", "case_runner.py")


class TestCaseRunnerSubprocess(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)

        self.agent_model = self._write_yaml(
            "agent.yaml", {"generator": "noop_agent"})
        self.user_model = self._write_yaml("user.yaml", {"generator": "noop"})

        self.config = {
            "orchestrator": "agent",
            "model_config": self.agent_model,
            "simulated_user_model_config": self.user_model,
            "dialects": ["postgres"],
            "database": "testdb",
            "scorers": {"turn_count": {}, "agent_steps": {}},
        }

    def _write_yaml(self, name, data):
        path = os.path.join(self.tmp, name)
        with open(path, "w") as f:
            yaml.safe_dump(data, f)
        return path

    def _case_dir(self, scenario, config=None):
        spec = EvalCaseSpec.build(
            scenario=scenario,
            config=config or self.config,
            job_id="job-xyz",
            run_time_iso="2026-09-10T12:00:00",
        )
        return spec.write_to_dir(os.path.join(self.tmp, "case"))

    def _run(self, case_dir):
        env = dict(os.environ)
        env["PYTHONPATH"] = os.pathsep.join(
            [_PACKAGE_ROOT, os.path.join(_PACKAGE_ROOT, "evalproto")]
        )
        env["EVALBENCH_CASE_ID"] = "test-case"
        env["EVALBENCH_WORKER_POOL"] = "pool-a"
        return subprocess.run(
            [sys.executable, _CASE_RUNNER, f"--case_dir={case_dir}"],
            capture_output=True,
            text=True,
            cwd=_REPO_ROOT,
            env=env,
            timeout=300,
        )

    def test_runs_a_case_and_emits_a_parseable_payload(self):
        case_dir = self._case_dir(
            {"id": "cuj_01", "starting_prompt": "hello", "max_turns": 1})
        proc = self._run(case_dir)

        payload = extract_result_payload(proc.stdout)
        self.assertIsNotNone(
            payload,
            f"no payload in stdout.\nstdout={proc.stdout}\nstderr={proc.stderr}",
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        self.assertIsNone(payload["error"])
        self.assertEqual(payload["case_id"], "cuj_01")
        self.assertEqual(len(payload["agent_results"]), 1)
        self.assertEqual(payload["agent_results"][0]["eval_id"], "cuj_01")
        self.assertTrue(payload["scoring_results"])

    def test_logs_go_to_stderr_so_the_payload_stays_clean(self):
        case_dir = self._case_dir({"id": "cuj_01", "starting_prompt": "hi"})
        proc = self._run(case_dir)

        begin = proc.stdout.index(RESULT_BEGIN) + len(RESULT_BEGIN)
        end = proc.stdout.index(RESULT_END)
        # Everything between the sentinels must be exactly the JSON payload.
        json.loads(proc.stdout[begin:end].strip())
        self.assertIn("case: running cuj_01", proc.stderr)

    def test_reports_a_bad_case_dir_as_a_payload_error_not_a_silent_crash(self):
        proc = self._run(os.path.join(self.tmp, "does-not-exist"))

        payload = extract_result_payload(proc.stdout)
        self.assertIsNotNone(payload, proc.stderr)
        self.assertEqual(proc.returncode, 1)
        self.assertIn("FileNotFoundError", payload["error"])
        self.assertEqual(payload["agent_results"], [])

    def test_creates_the_scenario_work_dir(self):
        work_dir = os.path.join(self.tmp, "work", "cuj")
        case_dir = self._case_dir(
            {
                "id": "cuj_01",
                "starting_prompt": "hi",
                "resolved_work_dir": work_dir,
            }
        )
        # build() re-roots the work dir into the container namespace; read the
        # rewritten value back rather than assuming the original path.
        spec = EvalCaseSpec.load_from_dir(case_dir)
        rerooted = spec.scenario["resolved_work_dir"]
        self.addCleanup(shutil.rmtree, rerooted, ignore_errors=True)

        proc = self._run(case_dir)
        self.assertIsNotNone(extract_result_payload(proc.stdout), proc.stderr)
        self.assertTrue(os.path.isdir(rerooted))


if __name__ == "__main__":
    unittest.main()
