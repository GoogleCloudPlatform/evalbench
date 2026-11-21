"""Tests for the ${VAR} placeholders of a dataset."""

import json
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

from dataset import dataset
from dataset.dataset import _expand_env_placeholders, load_gemini_cli_json

_ENV = {
    "EVAL_GCP_PROJECT_ID": "my-project",
    "SERVER_SECRET": "secret-value",
    "TEAM_VAR": "team-value",
}


def _expand(text, argv0, setting=None):
    env = dict(_ENV)
    if setting is not None:
        env[dataset.SERVER_ENV_VARS_SETTING] = setting
    with patch.dict(os.environ, env), patch.object(sys, "argv", [argv0]):
        os.environ.pop("NOT_SET_VAR", None)
        if setting is None:
            os.environ.pop(dataset.SERVER_ENV_VARS_SETTING, None)
        return _expand_env_placeholders(text, "dataset.json")


class LocalRunTest(unittest.TestCase):

    def test_expands_each_variable(self):
        self.assertEqual(
            _expand("${EVAL_GCP_PROJECT_ID} ${SERVER_SECRET}", "evalbench.py"),
            "my-project secret-value",
        )

    def test_unresolved_placeholder_fails(self):
        with self.assertRaisesRegex(ValueError, "NOT_SET_VAR.*Export"):
            _expand("${NOT_SET_VAR}", "evalbench.py")


class EvalServerTest(unittest.TestCase):

    ARGV0 = "/evalbench/evalbench/eval_server.py"

    def test_expands_a_variable_in_the_default_list(self):
        self.assertEqual(
            _expand("p=${EVAL_GCP_PROJECT_ID}", self.ARGV0), "p=my-project")

    def test_rejects_a_variable_that_is_not_in_the_list(self):
        with self.assertRaises(ValueError) as raised:
            _expand("${EVAL_GCP_PROJECT_ID} ${SERVER_SECRET}", self.ARGV0)

        message = str(raised.exception)
        self.assertIn("SERVER_SECRET", message)
        self.assertIn(dataset.SERVER_ENV_VARS_SETTING, message)
        self.assertNotIn("secret-value", message)

    def test_does_not_expand_a_variable_without_braces(self):
        self.assertEqual(
            _expand("$SERVER_SECRET $EVAL_GCP_PROJECT_ID", self.ARGV0),
            "$SERVER_SECRET $EVAL_GCP_PROJECT_ID",
        )

    def test_setting_replaces_the_default_list(self):
        self.assertEqual(
            _expand("${TEAM_VAR}", self.ARGV0, setting="TEAM_VAR, OTHER"),
            "team-value",
        )
        with self.assertRaises(ValueError):
            _expand("${EVAL_GCP_PROJECT_ID}", self.ARGV0, setting="TEAM_VAR")
        with self.assertRaises(ValueError):
            _expand("${EVAL_GCP_PROJECT_ID}", self.ARGV0, setting="")

    def test_unset_variable_in_the_list_fails(self):
        with self.assertRaises(ValueError):
            _expand("${NOT_SET_VAR}", self.ARGV0, setting="NOT_SET_VAR")

    def test_dataset_load_does_not_return_a_server_variable(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "evalset.json")
            with open(path, "w") as f:
                json.dump({"scenarios": [{
                    "id": "s1",
                    "starting_prompt": "${SERVER_SECRET}",
                }]}, f)

            with patch.dict(os.environ, _ENV), patch.object(
                    sys, "argv", [self.ARGV0]):
                with self.assertRaises(ValueError):
                    load_gemini_cli_json(path, {})


if __name__ == "__main__":
    unittest.main()
