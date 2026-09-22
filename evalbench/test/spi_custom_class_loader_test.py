import threading
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
        with self.assertRaises(ImportError):
            load_custom_class("nonexistent_module.FakeClass")

    def test_load_custom_class_attr_not_found(self):
        with self.assertRaises(AttributeError):
            load_custom_class("unittest.mock:NonExistentAttribute12345")

    def test_get_database_with_connector_class(self):
        config = {
            "db_type": "custom",
            "connector_class": f"{__name__}:DummyCustomConnector",
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
            "generator_class": f"{__name__}:DummyCustomGenerator",
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
            "connector_class": f"{__name__}:DummyCustomConnector",
        }
        with self.assertLogs("root", level="WARNING") as cm:
            db = get_database(config, "test_db")
            self.assertIsInstance(db, DummyCustomConnector)
            self.assertTrue(any("overriding db_type" in msg for msg in cm.output))

    @patch("generators.models.load_yaml_config")
    def test_get_generator_precedence_logging(self, mock_load_yaml):
        mock_load_yaml.return_value = {
            "generator": "noop",
            "generator_class": f"{__name__}:DummyCustomGenerator",
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
            "connector_class": f"{__name__}:BareMinimumConnector",
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
            "generator_class": f"{__name__}:BareMinimumGenerator",
        }
        global_models = {"registered_models": {}, "lock": threading.Lock()}
        model = get_generator(global_models, "dummy_bare_generator.yaml")
        self.assertIsInstance(model, BareMinimumGenerator)
        output = model.generate("write a query")
        self.assertEqual(output, "SELECT 1")


if __name__ == "__main__":
    unittest.main()
