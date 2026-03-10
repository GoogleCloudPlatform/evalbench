import unittest
from evalbench.util.instantiate_schemas import parse_textproto_to_dataclass

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
        import os
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

if __name__ == "__main__":
    unittest.main()
