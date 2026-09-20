import threading
import unittest
from unittest.mock import patch

from databases import get_database, _load_custom_class as _load_db_custom_class
from generators.models import get_generator, _load_custom_class as _load_model_custom_class


class DummyCustomConnector:

    def __init__(self, db_config):
        self.config = db_config


class DummyCustomGenerator:

    def __init__(self, config):
        self.config = config


class TestSPICustomClassLoader(unittest.TestCase):

    def test_load_custom_class_colon_syntax(self):
        cls = _load_db_custom_class("unittest.mock:MagicMock")
        from unittest.mock import MagicMock
        self.assertIs(cls, MagicMock)

    def test_load_custom_class_dot_syntax(self):
        cls = _load_model_custom_class("unittest.mock.MagicMock")
        from unittest.mock import MagicMock
        self.assertIs(cls, MagicMock)

    def test_load_custom_class_invalid_path(self):
        with self.assertRaises(ValueError):
            _load_db_custom_class("InvalidClassNameWithoutModule")

    def test_load_custom_class_module_not_found(self):
        with self.assertRaises(ImportError):
            _load_db_custom_class("nonexistent_module.FakeClass")

    def test_load_custom_class_attr_not_found(self):
        with self.assertRaises(AttributeError):
            _load_db_custom_class("unittest.mock:NonExistentAttribute12345")

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


if __name__ == "__main__":
    unittest.main()
