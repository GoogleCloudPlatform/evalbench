"""The bat-interact example dataset loads with the bird-interact loader."""

import os
import sys
import unittest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dataset.dataset import load_dataset_from_json

_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)
_DATASET_DIR = os.path.join(_REPO_ROOT, "datasets", "bat-interact")


class TestBatInteractDataset(unittest.TestCase):
    def test_every_line_loads_as_one_item(self):
        prompts = os.path.join(_DATASET_DIR, "prompts.jsonl")
        config = {
            "dataset_directory": _DATASET_DIR,
            "dataset_format": "bird-interact-format",
        }

        items = load_dataset_from_json(prompts, config)

        with open(prompts, "r") as f:
            num_lines = sum(1 for line in f if line.strip())
        loaded = [item for query_type in items.values() for item in query_type]
        self.assertEqual(len(loaded), num_lines)
        self.assertEqual(len({item.id for item in loaded}), num_lines)


if __name__ == "__main__":
    unittest.main()
