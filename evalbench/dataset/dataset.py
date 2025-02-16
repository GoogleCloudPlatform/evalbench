"""Process datasets."""

from typing import Any
from absl import app
from absl import flags
import json
import logging
from collections.abc import Sequence
from dataset.evalinput import EvalInputRequest
from dataset.evaloutput import EvalOutput


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

    if "db_id" in all_items[0].keys():
        logging.info("dataset in BIRD Format.")
        dialect = experiment_config["dialect"]
        input_items = load_dataset_from_bird(all_items, dialect)
    elif "nl_prompt" in all_items[0].keys():
        logging.info("dataset in new Evalbench Format")
        dialect = experiment_config["dialect"]
        input_items = load_dataset_from_newFormat(all_items, dialect)
    else:
        logging.info("dataset in regular Format.")
        input_items = load_dataset_from_regular(all_items)

    totalEntries = sum(len(input_items.get(q, [])) for q in ["dql", "dml", "ddl"])
    logging.info(f"Converted {totalEntries} entries to EvalInput.")

    return input_items, input_items['dql'][0].database


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
            other=build_normalized_other(item["other"])
        )
        input_items[eval_input.query_type].append(eval_input)
    return input_items


def build_normalized_other(other: dict[str, Any]):
    return {key: json.dumps(value) for key, value in other.items()}


def load_dataset_from_regular(dataset: Sequence[dict]):
    input_items = {"dql": [], "dml": [], "ddl": []}
    gen_id = 1
    for item in dataset:
        eval_input = EvalInputRequest(
            id=gen_id,
            query_type=item["query_type"],
            database=item["database"],
            nl_prompt=item["prompt"],
            dialects=item["dialects"],
            golden_sql=item["examples"][0],
            eval_query=item["eval_query"],
            setup_sql=item["setup_sql"],
            cleanup_sql=item["cleanup_sql"],
            tags=item["tags"],
            other={}
        )
        gen_id = gen_id + 1
        input_items[eval_input.query_type].append(eval_input)
    return input_items


def load_dataset_from_bird(dataset: Sequence[dict], dialect: str):
    input_items = {"dql": [], "dml": [], "ddl": []}
    for item in dataset:
        if item["result_matched"]:
            if dialect == "postgres":
                golden_sql = item["Postgres_query"]
            elif dialect == "sqlserver":
                golden_sql = item["SQLite_query"]
            else:
                golden_sql = item["Postgres_query"]
            eval_input = EvalInputRequest(
                id=item["question_id"],
                query_type="dql",
                database=item["db_id"],
                nl_prompt=" ".join([item["question"], item["evidence"]]).replace("`", '"'),
                dialects=[dialect],
                golden_sql=golden_sql,
                eval_query="",
                setup_sql="",
                cleanup_sql="",
                tags=[item["difficulty"]],
                other={}
            )
            input_items[eval_input.query_type].append(eval_input)
    return input_items


def breakdown_datasets_by_query_type(
    total_dataset: list[EvalInputRequest]
):
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


def main(argv: Sequence[str]) -> None:
    logging.info("Dataset converter v1.0.0")
    logging.info("Loading dataset from %s", _SOURCE_DATASET_PATH.value)
    dataset = load_dataset_from_json(_SOURCE_DATASET_PATH.value)
    logging.info("Loaded %d entries.", len(dataset))
    evaloutput = EvalOutput(dataset[0])
    logging.info("Done.")


if __name__ == "__main__":
    app.run(main)
