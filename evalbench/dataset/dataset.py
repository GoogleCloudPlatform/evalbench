"""Process datasets."""

from typing import Any
from absl import app
from absl import flags
import json
import logging
from collections.abc import Sequence
from dataset.evalinput import EvalInputRequest
from dataset.evaloutput import EvalOutput
from itertools import chain


_SOURCE_DATASET_PATH = flags.DEFINE_string(
    "source_dataset_path",
    "datasets/bird_pg_dev/financial.json",
    "Path to the source dataset configuration file.",
)


def load_json(json_file_path):
    all_items = []
    json_file_path = f"{json_file_path}"
    with open(json_file_path, "r") as json_file:
        all_items.extend(json.load(json_file))
    return all_items


def load_dataset_from_json(json_file_path, experiment_config):
    input_items = []
    all_items = load_json(json_file_path)
    if "nl_prompt" in all_items[0].keys():

        logging.info("dataset in new Evalbench Format")
        dialect = experiment_config["dialect"]
        input_items = load_dataset_from_newFormat(all_items, dialect)
    else:
        raise ValueError("Dataset not in new Evalbench Format")
    totalEntries = sum(len(input_items.get(q, [])) for q in ["dql", "dml", "ddl"])
    logging.info(f"Converted {totalEntries} entries to EvalInput.")

    return input_items


def load_dataset_from_newFormat(dataset: Sequence[dict], dialect: str):
    input_items = {"dql": [], "dml": [], "ddl": []}
    for item in dataset:
        eval_input = EvalInputRequest(
            id=item["id"],
            nl_prompt=item["nl_prompt"],
            query_type=item["query_type"].lower(),
            database=item["database"],
            dialects=item["dialects"],
            golden_sql=item["golden_sql"].get(dialect, []),
            eval_query=item["eval_query"].get(dialect, []),
            setup_sql=item["setup_sql"].get(dialect, []),
            cleanup_sql=item["cleanup_sql"].get(dialect, []),
            tags=item["tags"],
            other=build_normalized_other(item["other"]),
        )
        input_items[eval_input.query_type].append(eval_input)
    return input_items


def build_normalized_other(other: dict[str, Any]):
    return {key: json.dumps(value) for key, value in other.items()}


def breakdown_datasets_by_query_type(total_dataset: list[EvalInputRequest]):
    total_dataset_len = 0
    dataset: dict[str, list[EvalInputRequest]] = {
        "dql": [],
        "dml": [],
        "ddl": [],
    }
    for input in total_dataset:
        dataset[input.query_type].append(input)
        total_dataset_len += 1
    return dataset, total_dataset_len


def flatten_dataset(dataset: dict[str, list]):
    return list(chain.from_iterable(dataset.values()))


def main(argv: Sequence[str]) -> None:
    logging.info("Dataset converter v1.0.0")
    logging.info("Loading dataset from %s", _SOURCE_DATASET_PATH.value)
    dataset = load_dataset_from_json(_SOURCE_DATASET_PATH.value)
    logging.info("Loaded %d entries.", len(dataset))
    evaloutput = EvalOutput(dataset[0])
    logging.info("Done.")


if __name__ == "__main__":
    app.run(main)
