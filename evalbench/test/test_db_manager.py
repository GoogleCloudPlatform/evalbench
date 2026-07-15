import unittest
from unittest.mock import patch, MagicMock
import os
import sys

# Ensure evalbench is in sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))

from evalbench.evaluator.db_manager import _get_setup_values


class TestDbManagerSetup(unittest.TestCase):

    @patch('evalbench.evaluator.db_manager.load_setup_scripts')
    @patch('evalbench.evaluator.db_manager.load_db_data_from_csvs')
    @patch('os.path.isdir')
    def test_get_setup_values_with_dialect_exists(self, mock_isdir, mock_load_data, mock_load_scripts):
        # Setup: dialect directory exists
        mock_isdir.side_effect = lambda path: "spanner_gsql" in path

        setup_config = {"setup_directory": "setup"}
        db_name = "test_db"
        db_type = "spanner"
        dialect = "spanner_gsql"

        mock_load_scripts.return_value = (["pre"], ["setup"], ["post"])
        mock_load_data.return_value = {"table1": ["data"]}

        setup_scripts, data = _get_setup_values(setup_config, db_name, db_type, dialect)

        # Assert: loaded from spanner_gsql
        mock_load_scripts.assert_called_once_with("setup/test_db/spanner_gsql")
        mock_load_data.assert_called_once_with("setup/test_db/data")
        self.assertEqual(setup_scripts, (["pre"], ["setup"], ["post"]))
        self.assertEqual(data, {"table1": ["data"]})

    @patch('evalbench.evaluator.db_manager.load_setup_scripts')
    @patch('evalbench.evaluator.db_manager.load_db_data_from_csvs')
    @patch('os.path.isdir')
    def test_get_setup_values_with_dialect_missing_fallback(self, mock_isdir, mock_load_data, mock_load_scripts):
        # Setup: dialect directory does not exist, but db_type directory exists
        mock_isdir.return_value = False

        setup_config = {"setup_directory": "setup"}
        db_name = "test_db"
        db_type = "spanner"
        dialect = "spanner_gsql"

        mock_load_scripts.return_value = (["pre"], ["setup"], ["post"])
        mock_load_data.return_value = {"table1": ["data"]}

        setup_scripts, data = _get_setup_values(setup_config, db_name, db_type, dialect)

        # Assert: loaded from fallback spanner
        mock_load_scripts.assert_called_once_with("setup/test_db/spanner")
        mock_load_data.assert_called_once_with("setup/test_db/data")

    @patch('evalbench.evaluator.db_manager.load_setup_scripts')
    @patch('evalbench.evaluator.db_manager.load_db_data_from_csvs')
    @patch('os.path.isdir')
    def test_get_setup_values_no_dialect(self, mock_isdir, mock_load_data, mock_load_scripts):
        setup_config = {"setup_directory": "setup"}
        db_name = "test_db"
        db_type = "spanner"

        mock_load_scripts.return_value = (["pre"], ["setup"], ["post"])
        mock_load_data.return_value = {"table1": ["data"]}

        setup_scripts, data = _get_setup_values(setup_config, db_name, db_type, None)

        # Assert: loaded from spanner
        mock_load_scripts.assert_called_once_with("setup/test_db/spanner")
        mock_load_data.assert_called_once_with("setup/test_db/data")


if __name__ == '__main__':
    unittest.main()
