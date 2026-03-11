import unittest
from unittest.mock import patch, MagicMock
from evalbench.util.instantiate_schemas import instantiate_schemas
import os

class TestInstantiateSchemas(unittest.TestCase):
    @patch('evalbench.util.instantiate_schemas.get_database')
    @patch('evalbench.util.instantiate_schemas.load_yaml_config')
    @patch('evalbench.util.instantiate_schemas.load_dataset_from_json')
    @patch('evalbench.util.instantiate_schemas._get_setup_values')
    def test_instantiate_schemas_standard(self, mock_get_setup_values, mock_load_dataset, mock_load_yaml, mock_get_db):
        mock_load_yaml.side_effect = [
            {"dataset_config": "fake_ds.json", "database_configs": ["fake_db_config.yaml"], "setup_directory": "/tmp/setup"},
            {"db_type": "postgres"}
        ]
        
        mock_item = MagicMock()
        mock_item.database = "test_db"
        mock_load_dataset.return_value = [mock_item]
        
        mock_db = MagicMock()
        mock_get_db.return_value = mock_db
        
        mock_get_setup_values.return_value = (["pre"], ["setup"], ["post"]), {"table1": ["data"]}
        
        instantiate_schemas("dummy_config.yaml")
        
        mock_db.ensure_database_exists.assert_called_once_with("test_db")
        mock_db.set_setup_instructions.assert_called_once_with((["pre"], ["setup"], ["post"]), {"table1": ["data"]})
        mock_db.resetup_database.assert_called_once_with(force=True, setup_users=False)

if __name__ == "__main__":
    unittest.main()
