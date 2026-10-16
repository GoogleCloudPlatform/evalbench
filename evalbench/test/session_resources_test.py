"""Tests for the split between the evaluator-only resources root and the
agent sandbox root.

Client-uploaded resources (scenario JSON, rubrics, validators, configs) are
the answer key. They must never be reachable by a relative path from the
agent's ``fake_home``, which is where the CLI's cwd and HOME point.
"""

import os
import tempfile
import types
import unittest
from unittest.mock import patch

from util import sessionmgr
from util import config as config_util


def _resource(address, content=b"x"):
    return types.SimpleNamespace(address=address, content=content)


class TestSessionRootsAreDisjoint(unittest.TestCase):

    def test_resources_root_is_not_inside_or_around_the_sandbox_root(self):
        resources = os.path.abspath(sessionmgr.SESSION_RESOURCES_PATH)
        sandbox = os.path.abspath(sessionmgr.SESSION_SANDBOX_PATH)
        self.assertNotEqual(resources, sandbox)
        common = os.path.commonpath([resources, sandbox])
        # Neither root may contain the other.
        self.assertNotIn(common, (resources, sandbox))
        # And they must not be siblings either: `ls ../..` from fake_home
        # would otherwise list the resources root.
        self.assertNotEqual(os.path.dirname(resources), os.path.dirname(sandbox))


class TestSessionResourceStaging(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.resources_root = os.path.join(self.tmp.name, "resources")
        self.sandbox_root = os.path.join(self.tmp.name, "sandbox")
        self.sid = "sid-123"

        self._patches = [
            patch.object(sessionmgr, "SESSION_RESOURCES_PATH", self.resources_root),
            patch.object(sessionmgr, "SESSION_SANDBOX_PATH", self.sandbox_root),
            patch.object(config_util, "SESSION_RESOURCES_PATH", self.resources_root),
        ]
        for p in self._patches:
            p.start()

        self.manager = sessionmgr.SessionManager()
        self.fake_home = os.path.join(self.sandbox_root, self.sid, "fake_home")
        os.makedirs(self.fake_home)

    def tearDown(self):
        self.manager.shutdown()
        for p in self._patches:
            p.stop()
        self.tmp.cleanup()

    def test_resources_are_staged_outside_the_sandbox_tree(self):
        rubric = "google3/evals/rubrics/criteria.yaml"
        self.manager.write_resource_files(
            self.sid, [_resource(rubric, b"rubric_items: []")])

        staged = os.path.join(self.resources_root, self.sid, rubric)
        self.assertTrue(os.path.exists(staged))

        # The sandbox's parent holds only the sandbox; nothing uploaded by
        # the client is reachable by walking up from fake_home.
        self.assertEqual(
            os.listdir(os.path.dirname(self.fake_home)), ["fake_home"])
        self.assertFalse(os.path.exists(
            os.path.join(self.fake_home, "..", "google3")))
        self.assertFalse(
            os.path.abspath(staged).startswith(
                os.path.abspath(self.sandbox_root)))

    def test_session_resources_dir_is_private(self):
        self.manager.write_resource_files(
            self.sid, [_resource("google3/run.yaml")])
        mode = os.stat(os.path.join(self.resources_root, self.sid)).st_mode
        self.assertEqual(mode & 0o777, 0o700)

    def test_config_paths_are_rewritten_into_the_resources_root(self):
        experiment = {
            "dataset_config": "google3/evals/scenarios/case.json",
            "scorers": {
                "python_scorer": {
                    "script_path": "google3/validators/rubric_validator.py",
                    "rubrics": ["google3/evals/rubrics/criteria.yaml"],
                }
            },
        }
        config_util.update_google3_relative_paths(experiment, self.sid, {})

        rewritten = [
            experiment["dataset_config"],
            experiment["scorers"]["python_scorer"]["script_path"],
            experiment["scorers"]["python_scorer"]["rubrics"][0],
        ]
        for path in rewritten:
            self.assertTrue(
                path.startswith(os.path.join(self.resources_root, self.sid)),
                path)
            self.assertFalse(
                os.path.abspath(path).startswith(
                    os.path.abspath(self.sandbox_root)), path)

    def test_prune_removes_both_roots_without_following_symlinks(self):
        self.manager.write_resource_files(
            self.sid, [_resource("google3/skills/sql/SKILL.md")])
        skill_src = os.path.join(
            self.resources_root, self.sid, "google3", "skills", "sql")

        # `gemini skills link` leaves a symlink in the sandbox pointing at
        # the staged skill; a shared directory outside both roots stands in
        # for anything else an agent might link to.
        shared = os.path.join(self.tmp.name, "shared")
        os.makedirs(shared)
        with open(os.path.join(shared, "keep.txt"), "w") as f:
            f.write("keep")
        skills_dir = os.path.join(self.fake_home, ".gemini", "skills")
        os.makedirs(skills_dir)
        os.symlink(skill_src, os.path.join(skills_dir, "sql"))
        os.symlink(shared, os.path.join(skills_dir, "shared"))

        self.manager.prune_resource_files(self.sid)

        self.assertFalse(os.path.exists(
            os.path.join(self.resources_root, self.sid)))
        self.assertFalse(os.path.exists(
            os.path.join(self.sandbox_root, self.sid)))
        self.assertTrue(os.path.exists(os.path.join(shared, "keep.txt")))

    def test_prune_tolerates_missing_session(self):
        self.manager.prune_resource_files("never-created")

    def test_sessions_on_either_root_are_reloaded_after_restart(self):
        self.manager.write_resource_files(
            "resources-only", [_resource("google3/run.yaml")])
        os.makedirs(os.path.join(self.sandbox_root, "sandbox-only", "fake_home"))

        restarted = sessionmgr.SessionManager()
        try:
            self.assertIn("resources-only", restarted.get_sessions())
            self.assertIn("sandbox-only", restarted.get_sessions())
        finally:
            restarted.shutdown()


if __name__ == "__main__":
    unittest.main()
