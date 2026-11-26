"""Tests for file names that come from a dataset or a client."""

import os
import sqlite3
import tempfile
import unittest
from types import SimpleNamespace

from container import case_runner
from container.spec import _collect_env_files
from databases.sqlite import SQLiteDB
from scorers.sqlite_bridge import get_sqlite_ground_truth
from util.safepath import contained_path


class _TempDirTestCase(unittest.TestCase):

    def setUp(self):
        temp_dir = tempfile.TemporaryDirectory()
        self.addCleanup(temp_dir.cleanup)
        self.tmp = temp_dir.name
        self.base = os.path.join(self.tmp, "base")
        self.outside = os.path.join(self.tmp, "outside")
        os.makedirs(self.base)
        os.makedirs(self.outside)

    def _files_outside(self):
        return sorted(os.listdir(self.outside))


class ContainedPathTest(_TempDirTestCase):

    def test_returns_the_joined_path(self):
        for name in ("a.txt", "sub/a.txt", "sub/../a.txt"):
            self.assertEqual(
                contained_path(self.base, name), os.path.join(self.base, name))

    def test_rejects_names_that_leave_the_base(self):
        names = [
            "", ".", "..", "../outside/a.txt", "sub/../../outside/a.txt",
            os.path.join(self.outside, "a.txt"), "a\x00.txt", None, 5,
        ]
        for name in names:
            with self.assertRaises(ValueError, msg=repr(name)):
                contained_path(self.base, name)

    def test_rejects_a_symlink_that_leaves_the_base(self):
        os.symlink(self.outside, os.path.join(self.base, "link"))

        with self.assertRaises(ValueError):
            contained_path(self.base, "link/a.txt")


class ContainerEnvFilesTest(_TempDirTestCase):

    def setUp(self):
        super().setUp()
        self.env_dir = os.path.join(self.base, "env_files")
        os.makedirs(os.path.join(self.env_dir, "env"))
        with open(os.path.join(self.env_dir, "env", "ok.py"), "w") as f:
            f.write("ok")
        self.host_file = os.path.join(self.outside, "host.txt")
        with open(self.host_file, "w") as f:
            f.write("HOST_FILE")

    def test_collect_skips_files_outside_the_env_files_directory(self):
        scenario = {
            "id": "s",
            "env_files": ["../../outside/host.txt", self.host_file, "env/ok.py"],
        }

        self.assertEqual(
            _collect_env_files(scenario, self.base), {"env/ok.py": "ok"})

    def test_materialize_skips_files_outside_the_env_files_directory(self):
        spec = SimpleNamespace(env_files={
            "../../outside/written.txt": "x",
            os.path.join(self.outside, "abs.txt"): "x",
            "env/new.py": "new",
        })

        case_runner._materialize_env_files(spec, self.base)

        self.assertEqual(self._files_outside(), ["host.txt"])
        with open(os.path.join(self.env_dir, "env", "new.py")) as f:
            self.assertEqual(f.read(), "new")


class SqliteDatabaseNameTest(_TempDirTestCase):

    def _db(self, database_name):
        return SQLiteDB({
            "db_type": "sqlite",
            "database_path": self.base,
            "database_name": database_name,
            "extension": ".sqlite",
            "max_executions_per_minute": 10,
        })

    def test_connects_to_a_database_in_the_database_path(self):
        db = self._db("good")

        with db.engine.connect():
            pass

        self.assertEqual(os.listdir(self.base), ["good.sqlite"])

    def test_rejects_a_database_name_outside_the_database_path(self):
        for name in ("../outside/evil", os.path.join(self.outside, "evil")):
            db = self._db(name)

            with self.assertRaises(ValueError):
                with db.engine.connect():
                    pass
            with self.assertRaises(ValueError):
                db.ensure_database_exists(name)
            with self.assertRaises(RuntimeError):
                db.create_tmp_database(name)

        self.assertEqual(self._files_outside(), [])

    def test_ground_truth_skips_a_database_outside_the_directory(self):
        path = os.path.join(self.outside, "other.sqlite")
        conn = sqlite3.connect(path)
        conn.execute("CREATE TABLE t (id INT)")
        conn.execute("INSERT INTO t VALUES (1)")
        conn.commit()
        conn.close()

        for name in ("../outside/other", os.path.join(self.outside, "other")):
            self.assertEqual(
                get_sqlite_ground_truth("SELECT * FROM t", name, self.base), [])


if __name__ == "__main__":
    unittest.main()
