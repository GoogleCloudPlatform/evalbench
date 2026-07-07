import os
import sqlite3
import tempfile
import unittest

from scorers.sqlite_bridge import get_sqlite_ground_truth


class TestSqliteBridge(unittest.TestCase):

    def setUp(self):
        self.test_dir = tempfile.TemporaryDirectory()
        self.db_dir = self.test_dir.name
        self.database_name = "test_db"

        # Create a mock sqlite database file.
        self.db_path = os.path.join(self.db_dir, f"{self.database_name}.sqlite")
        conn = sqlite3.connect(self.db_path)
        cursor = conn.cursor()
        cursor.execute("CREATE TABLE users (id INT, name TEXT)")
        cursor.execute("INSERT INTO users VALUES (1, 'Alice'), (2, 'Bob')")
        conn.commit()
        conn.close()

    def tearDown(self):
        self.test_dir.cleanup()

    def test_get_sqlite_ground_truth_uses_named_db(self):
        query = "SELECT * FROM users"
        results = get_sqlite_ground_truth(query, self.database_name, self.db_dir)

        self.assertEqual(len(results), 2)
        self.assertEqual(results[0], {"id": 1, "name": "Alice"})
        self.assertEqual(results[1], {"id": 2, "name": "Bob"})

    def test_get_sqlite_ground_truth_missing_db(self):
        # Database that does not exist should return empty list.
        results = get_sqlite_ground_truth(
            "SELECT * FROM users", "nonexistent_db", self.db_dir
        )
        self.assertEqual(results, [])


if __name__ == "__main__":
    unittest.main()
