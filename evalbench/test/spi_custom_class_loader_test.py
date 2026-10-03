import os
import sys
import tempfile
import threading
import types
import unittest
from unittest.mock import patch

import databases
import generators.models
from databases import get_database
from generators.models import get_generator
from util.class_loader import load_custom_class


class DummyCustomConnector:

    def __init__(self, db_config):
        self.config = db_config


class DummyCustomGenerator:

    def __init__(self, config):
        self.config = config


class BareMinimumConnector:
    """Bare-bones connector implementing only the minimum contract (execute)."""

    def __init__(self, db_config):
        self.config = db_config

    def execute(self, query: str, eval_query: str = None, **kwargs):
        return [{"col": 1}], None, None


class BareMinimumGenerator:
    """Bare-bones generator implementing only the minimum contract (generate)."""

    def __init__(self, config):
        self.config = config

    def generate(self, prompt: str, **kwargs):
        return "SELECT 1"


class TestSPICustomClassLoader(unittest.TestCase):

    def setUp(self):
        super().setUp()
        # In-memory virtual module to test custom class loading hermetically
        # without depending on test-runner import-path resolution.
        self.mock_pkg = types.ModuleType("virtual_custom_engine_pkg")
        self.mock_pkg.DummyCustomConnector = DummyCustomConnector
        self.mock_pkg.DummyCustomGenerator = DummyCustomGenerator
        self.mock_pkg.BareMinimumConnector = BareMinimumConnector
        self.mock_pkg.BareMinimumGenerator = BareMinimumGenerator
        self.patcher = patch.dict(sys.modules, {"virtual_custom_engine_pkg": self.mock_pkg})
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()
        super().tearDown()

    def test_load_custom_class_colon_syntax(self):
        cls = load_custom_class("unittest.mock:MagicMock")
        from unittest.mock import MagicMock
        self.assertIs(cls, MagicMock)

    def test_load_custom_class_dot_syntax(self):
        cls = load_custom_class("unittest.mock.MagicMock")
        from unittest.mock import MagicMock
        self.assertIs(cls, MagicMock)

    def test_backward_compatibility_aliases(self):
        self.assertIs(databases._load_custom_class, load_custom_class)
        self.assertIs(generators.models._load_custom_class, load_custom_class)

    def test_load_custom_class_invalid_path(self):
        with self.assertRaises(ValueError):
            load_custom_class("InvalidClassNameWithoutModule")

    def test_load_custom_class_module_not_found(self):
        with self.assertRaises(ImportError) as ctx:
            load_custom_class("nonexistent_module.FakeClass")
        self.assertIn("Failed to import module 'nonexistent_module'", str(ctx.exception))

    def test_load_custom_class_attr_not_found(self):
        with self.assertRaises(AttributeError):
            load_custom_class("unittest.mock:NonExistentAttribute12345")

    def test_load_custom_class_cwd_fallback(self):
        """Verifies that modules in os.getcwd() are loaded when cwd is absent from sys.path (e.g. uvx)."""
        orig_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp_dir:
            real_tmp = os.path.abspath(os.path.realpath(tmp_dir))
            pkg_dir = os.path.join(real_tmp, "local_uvx_pkg")
            os.makedirs(pkg_dir)
            with open(os.path.join(pkg_dir, "__init__.py"), "w", encoding="utf-8") as f:
                f.write("")
            with open(os.path.join(pkg_dir, "helper.py"), "w", encoding="utf-8") as f:
                f.write("HELPER_VAL = 'from_sibling'\n")
            with open(os.path.join(pkg_dir, "my_connector.py"), "w", encoding="utf-8") as f:
                f.write(
                    "from local_uvx_pkg.helper import HELPER_VAL\n\n\n"
                    "class LocalUvxConnector:\n"
                    "    TAG = HELPER_VAL\n"
                )

            clean_sys_path = [
                p for p in sys.path
                if p not in ("", ".") and os.path.abspath(p) != real_tmp
            ]
            try:
                os.chdir(real_tmp)
                with patch.object(sys, "path", clean_sys_path):
                    cls = load_custom_class("local_uvx_pkg.my_connector:LocalUvxConnector")
                    self.assertEqual(cls.TAG, "from_sibling")
                    self.assertEqual(sys.path[-1], real_tmp)
            finally:
                os.chdir(orig_cwd)
                for mod_key in list(sys.modules.keys()):
                    if mod_key == "local_uvx_pkg" or mod_key.startswith("local_uvx_pkg."):
                        sys.modules.pop(mod_key, None)

    def test_load_custom_class_cwd_already_in_sys_path_does_not_duplicate(self):
        """When cwd (or '' / '.') is already on sys.path and module is missing, raises without duplicating cwd."""
        orig_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp_dir:
            real_tmp = os.path.abspath(os.path.realpath(tmp_dir))
            try:
                os.chdir(real_tmp)
                for existing_entry in (real_tmp, "", "."):
                    path_with_cwd = [existing_entry]
                    with patch.object(sys, "path", path_with_cwd):
                        with self.assertRaises(ImportError) as ctx:
                            load_custom_class("missing_pkg_xyz.MissingClass")
                        self.assertIn("Failed to import module", str(ctx.exception))
                        self.assertEqual(sys.path, [existing_entry])
            finally:
                os.chdir(orig_cwd)

    def test_load_custom_class_transitive_missing_dependency_skips_fallback(self):
        """When the target module is found but fails on an inner import, does not mutate sys.path or retry."""
        orig_cwd = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp_dir:
            real_tmp = os.path.abspath(os.path.realpath(tmp_dir))
            mod_dir = os.path.join(real_tmp, "installed_dir")
            cwd_dir = os.path.join(real_tmp, "other_cwd")
            os.makedirs(mod_dir)
            os.makedirs(cwd_dir)
            with open(os.path.join(mod_dir, "broken_dep_mod.py"), "w", encoding="utf-8") as f:
                f.write("import nonexistent_third_party_lib_xyz\n")

            custom_sys_path = [mod_dir]
            try:
                os.chdir(cwd_dir)
                with patch.object(sys, "path", custom_sys_path):
                    with self.assertRaises(ImportError) as ctx:
                        load_custom_class("broken_dep_mod:SomeClass")
                    self.assertIn("nonexistent_third_party_lib_xyz", str(ctx.exception))
                    self.assertEqual(sys.path, [mod_dir])
            finally:
                os.chdir(orig_cwd)
                sys.modules.pop("broken_dep_mod", None)

    def test_get_database_with_connector_class(self):
        config = {
            "db_type": "custom",
            "connector_class": "virtual_custom_engine_pkg:DummyCustomConnector",
        }
        db = get_database(config, "test_db")
        self.assertIsInstance(db, DummyCustomConnector)
        self.assertEqual(db.config["database_name"], "test_db")

    def test_get_database_custom_missing_connector_class(self):
        config = {
            "db_type": "custom",
        }
        with self.assertRaises(ValueError) as ctx:
            get_database(config, "test_db")
        self.assertIn("connector_class", str(ctx.exception))

    @patch("generators.models.load_yaml_config")
    def test_get_generator_with_generator_class(self, mock_load_yaml):
        mock_load_yaml.return_value = {
            "generator": "custom",
            "generator_class": "virtual_custom_engine_pkg:DummyCustomGenerator",
        }
        global_models = {"registered_models": {}, "lock": threading.Lock()}
        model = get_generator(global_models, "dummy_path.yaml")
        self.assertIsInstance(model, DummyCustomGenerator)
        self.assertIn("dummy_path.yaml", global_models["registered_models"])

    @patch("generators.models.load_yaml_config")
    def test_get_generator_custom_missing_generator_class(self, mock_load_yaml):
        mock_load_yaml.return_value = {
            "generator": "custom",
        }
        global_models = {"registered_models": {}, "lock": threading.Lock()}
        with self.assertRaises(ValueError) as ctx:
            get_generator(global_models, "dummy_path.yaml")
        self.assertIn("generator_class", str(ctx.exception))

    def test_get_database_standard_type_backward_compatibility(self):
        # Existing configs with standard db_type and no connector_class
        config = {
            "db_type": "sqlite",
            "database_name": "test_db",
            "database_path": "/tmp",
            "max_executions_per_minute": 60,
            "connector_class": None,
        }
        db = get_database(config, "test_db")
        from databases.sqlite import SQLiteDB
        self.assertIsInstance(db, SQLiteDB)

    @patch("generators.models.load_yaml_config")
    def test_get_generator_standard_generator_backward_compatibility(self, mock_load_yaml):
        # Existing configs with standard generator and null generator_class
        mock_load_yaml.return_value = {
            "generator": "noop",
            "generator_class": None,
        }
        global_models = {"registered_models": {}, "lock": threading.Lock()}
        model = get_generator(global_models, "dummy_path.yaml")
        from generators.models.passthrough import NOOPGenerator
        self.assertIsInstance(model, NOOPGenerator)

    def test_get_database_precedence_logging(self):
        config = {
            "db_type": "sqlite",
            "database_name": "test_db",
            "database_path": "/tmp",
            "max_executions_per_minute": 60,
            "connector_class": "virtual_custom_engine_pkg:DummyCustomConnector",
        }
        with self.assertLogs("root", level="WARNING") as cm:
            db = get_database(config, "test_db")
            self.assertIsInstance(db, DummyCustomConnector)
            self.assertTrue(any("overriding db_type" in msg for msg in cm.output))

    @patch("generators.models.load_yaml_config")
    def test_get_generator_precedence_logging(self, mock_load_yaml):
        mock_load_yaml.return_value = {
            "generator": "noop",
            "generator_class": "virtual_custom_engine_pkg:DummyCustomGenerator",
        }
        global_models = {"registered_models": {}, "lock": threading.Lock()}
        with self.assertLogs("root", level="WARNING") as cm:
            model = get_generator(global_models, "dummy_path.yaml")
            self.assertIsInstance(model, DummyCustomGenerator)
            self.assertTrue(any("overriding generator" in msg for msg in cm.output))

    def test_bare_minimum_connector_contract(self):
        """Verifies that a bare-bones custom connector with only execute works via get_database."""
        config = {
            "db_type": "custom",
            "database_name": "test_db",
            "connector_class": "virtual_custom_engine_pkg:BareMinimumConnector",
        }
        db = get_database(config, "test_db")
        self.assertIsInstance(db, BareMinimumConnector)
        result, eval_result, error = db.execute("SELECT 1")
        self.assertEqual(result, [{"col": 1}])
        self.assertIsNone(eval_result)
        self.assertIsNone(error)

    @patch("generators.models.load_yaml_config")
    def test_bare_minimum_generator_contract(self, mock_load_yaml):
        """Verifies that a bare-bones custom generator with only generate works via get_generator."""
        mock_load_yaml.return_value = {
            "generator": "custom",
            "generator_class": "virtual_custom_engine_pkg:BareMinimumGenerator",
        }
        global_models = {"registered_models": {}, "lock": threading.Lock()}
        model = get_generator(global_models, "dummy_bare_generator.yaml")
        self.assertIsInstance(model, BareMinimumGenerator)
        output = model.generate("write a query")
        self.assertEqual(output, "SELECT 1")


if __name__ == "__main__":
    unittest.main()
