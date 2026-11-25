"""Tests for the removal of secret values from the reported config."""

import copy
import unittest

from util.config import REDACTED, config_to_df, redact_secrets


def _reported(experiment_config=None, model_config=None, db_configs=None):
    """Returns the reported config as a {key: value} dict."""
    df = config_to_df(
        "job", None, experiment_config or {}, model_config or {},
        db_configs or [])
    return dict(zip(df["config"].tolist(), df["value"].tolist()))


class RedactSecretsTest(unittest.TestCase):

    def test_redacts_secret_keys(self):
        config = {
            "password": "pw",
            "CLOUD_SQL_POSTGRES_PASSWORD": "pw",
            "client_secret": "s",
            "GITHUB_TOKEN": "t",
            "accessToken": "t",
            "GOOGLE_API_KEY": "k",
            "x-api-key": "k",
            "apiKey": "k",
            "private_key": "k",
            "Authorization": "Bearer t",
            "credentials": {"user": "u", "value": "v"},
        }

        redacted = redact_secrets(config)

        self.assertEqual(set(redacted.values()), {REDACTED})

    def test_keeps_keys_that_are_not_secrets(self):
        config = {
            "token_consumption": {},
            "tokens_processed": {},
            "effective_billed_tokens": {},
            "max_tokens": 1024,
            "token_budget": 5,
            "secret_manager_path": "projects/p/secrets/s",
            "key": "primary",
            "partition_key": "id",
            "user_name": "evalbench",
        }

        self.assertEqual(redact_secrets(config), config)

    def test_keeps_empty_values_and_secret_references(self):
        config = {
            "password": "",
            "api_key": None,
            "use_token": False,
            "openai_api_key_secret": "projects/p/secrets/s/versions/1",
        }

        self.assertEqual(redact_secrets(config), config)

    def test_redacts_inside_lists_and_nested_dicts(self):
        config = {
            "db_configs": [{"user_name": "u", "password": "pw"}],
            "setup": {"mcp_servers": {"s": {"headers": {"Authorization": "t"}}}},
        }

        self.assertEqual(redact_secrets(config), {
            "db_configs": [{"user_name": "u", "password": REDACTED}],
            "setup": {
                "mcp_servers": {"s": {"headers": {"Authorization": REDACTED}}}
            },
        })

    def test_redacts_the_password_in_a_url(self):
        config = {
            "connection_string": "mongodb://user:pw@host:27017/db",
            "url": "https://example.com/a:b@c",
            "image": "repo/image:tag",
        }

        self.assertEqual(redact_secrets(config), {
            "connection_string": f"mongodb://user:{REDACTED}@host:27017/db",
            "url": "https://example.com/a:b@c",
            "image": "repo/image:tag",
        })

    def test_does_not_change_the_input(self):
        config = {"db_configs": [{"password": "pw"}], "env": {"API_KEY": "k"}}
        original = copy.deepcopy(config)

        redact_secrets(config)

        self.assertEqual(config, original)


class ConfigToDfRedactionTest(unittest.TestCase):

    def test_report_has_no_secret_values(self):
        secrets = ["db-password", "api-key-value", "bearer-token"]
        reported = _reported(
            experiment_config={"orchestrator": "agent"},
            model_config={
                "generator": "gemini_cli",
                "env": {"GOOGLE_API_KEY": secrets[1]},
                "setup": {"mcp_servers": {
                    "s": {"headers": {"Authorization": secrets[2]}}}},
            },
            db_configs=[{"user_name": "u", "password": secrets[0]}],
        )

        for value in reported.values():
            for secret in secrets:
                self.assertNotIn(secret, str(value))
        self.assertEqual(reported["experiment_config.orchestrator"], "agent")
        self.assertEqual(reported["model_config.generator"], "gemini_cli")
        self.assertEqual(reported["model_config.env.GOOGLE_API_KEY"], REDACTED)
        self.assertIn("'user_name': 'u'", reported["db_configs"])


if __name__ == "__main__":
    unittest.main()
