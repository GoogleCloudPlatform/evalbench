import unittest
from unittest.mock import MagicMock

from util.config import config_to_df, with_agent_version


class TestWithAgentVersion(unittest.TestCase):

    def test_does_not_change_input(self):
        model_config = {"generator": "agy_cli"}
        with_agent_version(model_config, MagicMock(agent_version="agy@1.2.12"))
        self.assertNotIn("agent_version", model_config)

    def test_config_to_df_row(self):
        # The viewer reads this exact key.
        orchestrator = MagicMock(agent_version="agy@1.2.12")
        df = config_to_df(
            "job",
            None,
            {},
            with_agent_version({"generator": "agy_cli"}, orchestrator),
            [],
        )
        row = df[df["config"] == "model_config.agent_version"]
        self.assertEqual(row["value"].tolist(), ["agy@1.2.12"])


if __name__ == "__main__":
    unittest.main()
