import unittest
from unittest.mock import patch, MagicMock
from evalbench.util.instantiate_schemas import parse_textproto_to_dataclass, instantiate_schemas
import os

class TestInstantiateSchemas(unittest.TestCase):
    def test_parse_textproto_to_dataclass(self):
        # Create a dummy textproto content
        content = """
tables: {
  table: "users"
  columns: {
    column: "id"
    data_type: "INT64 NOT NULL"
  }
  columns: {
    column: "name"
    data_type: "STRING(MAX)"
  }
}
tables: {
  table: "posts"
  columns: {
    column: "post_id"
    data_type: "INT64"
  }
}
"""
        import tempfile
        with tempfile.NamedTemporaryFile(mode='w', delete=False) as f:
            f.write(content)
            temp_path = f.name
        
        try:
            schema = parse_textproto_to_dataclass(temp_path, "test_db")
            self.assertIsNotNone(schema)
            self.assertEqual(len(schema.tables), 2)
            self.assertEqual(schema.tables[0].name, "users")
            self.assertEqual(len(schema.tables[0].columns), 2)
            self.assertEqual(schema.tables[0].columns[0].name, "id")
            self.assertEqual(schema.tables[0].columns[0].type, "INT64")
            self.assertFalse(schema.tables[0].columns[0].is_nullable)
            self.assertEqual(schema.tables[1].name, "posts")
            self.assertTrue(schema.tables[1].columns[0].is_nullable)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @patch('evalbench.util.instantiate_schemas.get_database')
    @patch('evalbench.util.instantiate_schemas.load_yaml_config')
    @patch('evalbench.util.instantiate_schemas.load_dataset_from_json')
    @patch('evalbench.util.instantiate_schemas._get_setup_values')
    @patch('evalbench.util.instantiate_schemas.os.path.exists')
    def test_instantiate_schemas_standard(self, mock_exists, mock_get_setup_values, mock_load_dataset, mock_load_yaml, mock_get_db):
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
        
        mock_exists.return_value = False
        
        instantiate_schemas("dummy_config.yaml")
        
        mock_db.ensure_database_exists.assert_called_once_with("test_db")
        mock_db.set_setup_instructions.assert_called_once_with((["pre"], ["setup"], ["post"]), {"table1": ["data"]})
        mock_db.resetup_database.assert_called_once_with(force=True, setup_users=False)

if __name__ == "__main__":
    unittest.main()
