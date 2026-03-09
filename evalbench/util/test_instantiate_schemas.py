import unittest
from unittest.mock import patch, MagicMock
from evalbench.util.instantiate_schemas import proto_to_dataclass
from evalbench.evalproto import schema_pb2

class TestInstantiateSchemas(unittest.TestCase):
    def test_proto_to_dataclass(self):
        proto = schema_pb2.DatabaseSchema(name="test_db")
        t = proto.tables.add(name="users")
        t.columns.add(name="id", type="INT", is_primary_key=True)
        t.columns.add(name="name", type="VARCHAR")
        
        schema = proto_to_dataclass(proto)
        self.assertEqual(schema.name, "test_db")
        self.assertEqual(len(schema.tables), 1)
        self.assertEqual(schema.tables[0].name, "users")
        self.assertEqual(schema.tables[0].columns[0].name, "id")
        self.assertEqual(schema.tables[0].columns[0].type, "INT")
        self.assertTrue(schema.tables[0].columns[0].is_primary_key)
        self.assertEqual(schema.tables[0].columns[1].name, "name")

if __name__ == "__main__":
    unittest.main()
