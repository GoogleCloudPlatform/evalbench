import json
import os
import tempfile
import unittest

from dataset.dataset import load_knowledge


class TestLoadKnowledge(unittest.TestCase):

    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.dataset_dir = self._tmp.name
        self.database = "db_blog"
        self.db_dir = os.path.join(self.dataset_dir, self.database)
        os.makedirs(self.db_dir)
        self.kb_path = os.path.join(
            self.db_dir, f"{self.database}_kb.jsonl"
        )

    def tearDown(self):
        self._tmp.cleanup()

    def _write_kb(self, lines):
        with open(self.kb_path, "w", encoding="utf-8") as f:
            f.write("\n".join(lines))

    def _parse_result(self, result):
        if not result:
            return []
        return [json.loads(line) for line in result.split("\n")]

    def test_loads_all_entries(self):
        """Regression: every line must be loaded, not just the last one."""
        entries = [
            {"id": "k1", "fact": "a"},
            {"id": "k2", "fact": "b"},
            {"id": "k3", "fact": "c"},
        ]
        self._write_kb([json.dumps(e) for e in entries])

        result = load_knowledge(self.dataset_dir, self.database)

        self.assertEqual(self._parse_result(result), entries)

    def test_skips_blank_lines(self):
        entries = [{"id": "k1", "fact": "a"}, {"id": "k2", "fact": "b"}]
        self._write_kb(
            [json.dumps(entries[0]), "", "   ", json.dumps(entries[1]), ""]
        )

        result = load_knowledge(self.dataset_dir, self.database)

        self.assertEqual(self._parse_result(result), entries)

    def test_excludes_ambiguous_ids(self):
        entries = [
            {"id": "k1", "fact": "a"},
            {"id": "k2", "fact": "b"},
            {"id": "k3", "fact": "c"},
        ]
        self._write_kb([json.dumps(e) for e in entries])
        knowledge_ambiguity = [{"deleted_knowledge": "k2"}]

        result = load_knowledge(
            self.dataset_dir, self.database, knowledge_ambiguity
        )

        self.assertEqual(
            self._parse_result(result),
            [{"id": "k1", "fact": "a"}, {"id": "k3", "fact": "c"}],
        )

    def test_missing_file_returns_empty(self):
        result = load_knowledge(self.dataset_dir, "nonexistent_db")
        self.assertEqual(result, "")

    def test_blank_only_file_returns_empty(self):
        """A file with only blank lines must not raise and return ''."""
        self._write_kb(["", "   ", ""])

        result = load_knowledge(self.dataset_dir, self.database)

        self.assertEqual(result, "")


if __name__ == "__main__":
    unittest.main()
