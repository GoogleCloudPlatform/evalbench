"""Tests for the custom classes that the eval server can load."""

import collections
import collections.abc
import os
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch

from databases import get_database
from generators.models import get_generator
from util import class_loader
from util.class_loader import load_custom_class

_SERVER = "/evalbench/evalbench/eval_server.py"


def _load(class_path, argv0, modules=None):
    env = {} if modules is None else {
        class_loader.SERVER_MODULES_SETTING: modules}
    with patch.dict(os.environ, env), patch.object(sys, "argv", [argv0]):
        if modules is None:
            os.environ.pop(class_loader.SERVER_MODULES_SETTING, None)
        return load_custom_class(class_path)


class LocalRunTest(unittest.TestCase):

    def test_loads_each_class(self):
        self.assertIs(
            _load("collections:OrderedDict", "evalbench.py"),
            collections.OrderedDict)


class EvalServerTest(unittest.TestCase):

    def test_default_list_permits_no_class(self):
        for class_path in ("subprocess:Popen", "collections.OrderedDict"):
            with self.assertRaises(PermissionError) as raised:
                _load(class_path, _SERVER)
            self.assertIn(
                class_loader.SERVER_MODULES_SETTING, str(raised.exception))

    def test_loads_a_class_from_a_permitted_module(self):
        modules = "my_package, collections"

        self.assertIs(
            _load("collections:OrderedDict", _SERVER, modules),
            collections.OrderedDict)
        self.assertIs(
            _load("collections.abc:Mapping", _SERVER, modules),
            collections.abc.Mapping)

    def test_rejects_a_module_that_is_not_permitted(self):
        for class_path in ("subprocess:Popen", "collectionsx:OrderedDict",
                           "os.path:join"):
            with self.assertRaises(PermissionError):
                _load(class_path, _SERVER, "collections")

    def test_does_not_import_a_rejected_module(self):
        with patch.object(class_loader.importlib, "import_module") as imported:
            with self.assertRaises(PermissionError):
                _load("subprocess:Popen", _SERVER)

        imported.assert_not_called()

    def test_generator_class_does_not_run_on_the_server(self):
        with tempfile.TemporaryDirectory() as tmp:
            marker = os.path.join(tmp, "MARKER")
            config_path = os.path.join(tmp, "model.yaml")
            with open(config_path, "w") as f:
                # subprocess.Popen(config) uses the keys as the command.
                f.write(f'"/usr/bin/touch": 1\n"{marker}": 2\n'
                        'generator_class: "subprocess:Popen"\n')
            global_models = {
                "lock": threading.Lock(), "registered_models": {}}

            with patch.dict(os.environ), patch.object(sys, "argv", [_SERVER]):
                os.environ.pop(class_loader.SERVER_MODULES_SETTING, None)
                with self.assertRaises(PermissionError):
                    get_generator(global_models, config_path)

            self.assertFalse(os.path.exists(marker))

    def test_connector_class_does_not_load_on_the_server(self):
        db_config = {"connector_class": "subprocess:Popen"}

        with patch.dict(os.environ), patch.object(sys, "argv", [_SERVER]):
            os.environ.pop(class_loader.SERVER_MODULES_SETTING, None)
            with self.assertRaises(PermissionError):
                get_database(db_config, None)


if __name__ == "__main__":
    unittest.main()
