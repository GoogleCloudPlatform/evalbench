"""Process datasets."""

from typing import Any
import json
import logging
from collections.abc import Sequence
from dataset.evalinput import EvalInputRequest
from dataset.evalinteractinput import EvalInteractInputRequest
from itertools import chain


def load_schema(dataset_dir: str, selected_database: str):
    schema_path = f"{dataset_dir}/{selected_database}/{selected_database}_schema.txt"
    with open(schema_path, "r", encoding="utf-8") as f:
        schema = f.read()
    return schema


def load_knowledge(
    dataset_dir: str, selected_database: str, knowledge_ambiguity: list = []
):
    exclude_ids = []
    external_kg_list = []
    external_kg_path = f"{dataset_dir}/{selected_database}/{selected_database}_kb.jsonl"
    for knowledge_amb_i in knowledge_ambiguity:
        exclude_ids.append(knowledge_amb_i["deleted_knowledge"])

    with open(external_kg_path, "r", encoding="utf-8") as f:
        for line in f:
            if not line.strip():
                continue
            obj = json.loads(line)
            if obj.get("id") not in exclude_ids:
                external_kg_list.append(json.dumps(obj))

    external_kg = "\n".join(external_kg_list)
    return external_kg


def load_bird_interact_dataset(json_file_path, config):
    input_items: dict[str, list[EvalInteractInputRequest]] = {
        "dql": [],
        "dml": [],
        "ddl": [],
    }
    dataset_dir = config["dataset_directory"]
    max_turn = config.get("max_turn", 6)
    num_evals_to_run = config.get("num_evals_to_run", 0)

    with open(json_file_path, "r") as f:
        for line in f:
            item = json.loads(line)
            category = item["category"]
            if category == "Query":
                item["query_type"] = "dql"
            elif category == "Management":
                item["query_type"] = "ddl"
            else:
                logging.error("Unknown category: %s", category)

            selected_database = item["selected_database"]
            item["turn"] = 0
            item["schema"] = load_schema(dataset_dir, selected_database)
            item["knowledge"] = load_knowledge(
                dataset_dir, selected_database, item["knowledge_ambiguity"]
            )
            min_turn = len(item["user_query_ambiguity"]["critical_ambiguity"]) + len(
                item["knowledge_ambiguity"]
            )
            item["min_turn"] = min_turn
            item["max_turn"] = max_turn

            eval_input = EvalInteractInputRequest(
                id=item["instance_id"],
                amb_user_query=item["amb_user_query"],
                query_type=item["query_type"],
                eval_query="",
                database=item["selected_database"],
                dialects=["postgres"],
                tags=item["difficulty_tier"],
                payload=item,
            )
            input_items[eval_input.query_type].append(eval_input)
            if (
                num_evals_to_run > 0
                and len(input_items[eval_input.query_type]) == num_evals_to_run
            ):
                break

    return input_items


def load_json(json_file_path):
    all_items = []
    with open(json_file_path, "r") as json_file:
        all_items.extend(json.load(json_file))
    return all_items


def load_dataset_from_json(json_file_path, config):
    input_items = {}
    dataset_format = config.get("dataset_format", "evalbench-standard-format")
    if dataset_format == "bird-interact-format":
        all_items = load_bird_interact_dataset(json_file_path, config)
    else:
        all_items = load_json(json_file_path)

    if dataset_format == "evalbench-standard-format":
        config["orchestrator"] = "oneshot"
        input_items = load_dataset(all_items, config)
    elif dataset_format == "bird-standard-format":
        config["orchestrator"] = "oneshot"
        input_items = load_dataset_from_bird_format(all_items, config)
    elif dataset_format == "bird-interact-format":
        config["orchestrator"] = "interact"
        input_items = all_items
    else:
        raise ValueError("Dataset not in any of the recognised formats")

    totalEntries = sum(len(input_items.get(q, [])) for q in ["dql", "dml", "ddl"])
    logging.info(f"Converted {totalEntries} entries to EvalInput.")
    return input_items


def load_dataset_from_bird_format(dataset: Sequence[dict], config):
    input_items: dict[str, list[EvalInputRequest]] = {"dql": [], "dml": [], "ddl": []}
    dataset_config = config["dataset_config"]
    dataset_str = str(dataset_config).split("/")[-1].replace(".json", "")
    dialects = config["dialects"]
    query_type = "dql"
    for item in dataset:
        # Add "ifs" to handle situations when some keys do not in(or in different format of) the BIRD evaluation dataset
        if "question_id" not in item and "id" in item:
            item["question_id"] = item["id"]
        if "question" not in item and "other" in item:
            item["question"] = item["other"]["question"]
        if "evidence" not in item and "other" in item:
            item["evidence"] = item["other"]["evidence"]
        if "question" not in item and "other" in item:
            item["question"] = item["other"]["question"]
        if "db_id" not in item:
            item["db_id"] = dataset_str
        if "SQL" not in item:
            if dialects[0] in item["golden_sql"]:
                item["SQL"] = item["golden_sql"][dialects[0]]
            else:
                item["SQL"] = ""
        if "difficulty" not in item and "tags" in item:
            item["difficulty"] = item["tags"]

        if item["SQL"]:
            eval_input = EvalInputRequest(
                id=item["question_id"],
                nl_prompt="".join([item["question"], item["evidence"]]).replace(
                    "`", '"'
                ),
                query_type=query_type,
                database=item["db_id"],
                dialects=config["dialects"],
                golden_sql=item["SQL"],
                eval_query="",
                setup_sql="",
                cleanup_sql="",
                tags=[item["difficulty"]],
                other={},
            )
            input_items[eval_input.query_type].append(eval_input)
    return input_items


def load_dataset(dataset: Sequence[dict], config):
    input_items: dict[str, list[EvalInputRequest]] = {"dql": [], "dml": [], "ddl": []}
    for item in dataset:
        if not _item_meets_config_filters(item, config):
            continue
        eval_input = EvalInputRequest(
            id=item["id"],
            nl_prompt=item["nl_prompt"],
            query_type=item["query_type"].lower(),
            database=item["database"],
            dialects=_union_dialects(item["dialects"], config.get("dialects", [])),
            golden_sql=item["golden_sql"],
            eval_query=item["eval_query"],
            setup_sql=item["setup_sql"],
            cleanup_sql=item["cleanup_sql"],
            tags=item["tags"],
            other=build_normalized_other(item["other"]),
        )
        input_items[eval_input.query_type].append(eval_input)
    return input_items


def _union_dialects(item_dialects: list[str], config_dialects: list[str]):
    if not len(config_dialects):
        return item_dialects
    return list(set(item_dialects) & set(config_dialects))


def _item_meets_config_filters(item: dict, config: dict):
    if item["query_type"].lower() not in config.get(
        "query_types", ["dql", "dml", "ddl"]
    ):
        return False
    if len(config.get("databases", [])) and item["database"] not in config.get(
        "databases", []
    ):
        return False
    if len(config.get("dialects", [])):
        for dialect in item["dialects"]:
            if dialect in config.get("dialects", []):
                return True
    else:
        return True
    return False


def build_normalized_other(other: dict[str, Any]):
    return {key: json.dumps(value) for key, value in other.items()}


def breakdown_datasets(total_dataset: list[EvalInputRequest]):
    """
    The shape of the output will be dict[str, dict[str, list[EvalInputRequest]]]
    in the following format:
    {
      dialect (str):
      -> database (str):
          -> query_type (str; [dql,dml,ddl]):
              -> list[EvalInputRequest]
    }
    """
    total_dataset_len = 0
    total_db_len = 0
    datasets: dict[str, dict[str, dict[str, list[EvalInputRequest]]]] = {}
    for input in total_dataset:
        for dialect in input.dialects:
            if dialect not in datasets:
                datasets[dialect] = {}
        if input.database not in datasets[dialect]:
            datasets[dialect][input.database] = {}
        if input.query_type not in datasets[dialect][input.database]:
            datasets[dialect][input.database][input.query_type] = []
            total_db_len += 1
        datasets[dialect][input.database][input.query_type].append(
            input.copy_for_dialect(dialect)
        )
        total_dataset_len += 1
    return datasets, total_dataset_len, total_db_len


def flatten_dataset(dataset: dict[str, list]):
    return list(chain.from_iterable(dataset.values()))
