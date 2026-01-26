import datetime
import logging
import os
import csv
import random
import string
from typing import List

from pyaml_env import parse_config
import pandas as pd
from .sessionmgr import SESSION_RESOURCES_PATH
from google.protobuf import text_format

logging.getLogger().setLevel(logging.INFO)


def load_yaml_config(yaml_file):
    config = parse_config(yaml_file)
    return config


def load_textproto(textproto_file, text_proto_object):
    with open(textproto_file, "r") as file:
        text_format.Merge(file.read(), text_proto_object)
    return text_proto_object


def load_db_data_from_csvs(data_directory: str):
    tables: dict[str, List[str]] = {}
    if not os.path.isdir(data_directory):
        return tables
    for filename in os.listdir(data_directory):
        if filename.endswith(".csv"):
            table_name = filename[:-4]
            with open(os.path.join(data_directory, filename), "r") as csvfile:
                reader = csv.reader(csvfile)
                rows = []
                for row in reader:
                    rows.append(row)
                tables[table_name] = rows
    return tables


def load_setup_scripts(setup_scripts_directory_path: str):
    pre_setup = _load_setup_sql(
        os.path.join(setup_scripts_directory_path, "pre_setup.sql"),
    )
    setup = _load_setup_sql(
        os.path.join(setup_scripts_directory_path, "setup.sql"),
    )
    # Check for setup.json and append it if exists
    setup_json_path = os.path.join(setup_scripts_directory_path, "setup.json")
    if os.path.exists(setup_json_path):
        with open(setup_json_path, "r") as f:
            setup.append(f.read())

    # Check for post_setup.json
    post_setup_json_path = os.path.join(setup_scripts_directory_path, "post_setup.json")
    if os.path.exists(post_setup_json_path):
        import json
        with open(post_setup_json_path, "r") as f:
            # Load as list of dicts, then convert back to strings for batch_execute
            try:
                data = json.load(f)
                if isinstance(data, list):
                    post_setup = [json.dumps(item) for item in data]
                else:
                    post_setup = []
            except Exception:
                post_setup = []
    else:
        post_setup = _load_setup_sql(
            os.path.join(setup_scripts_directory_path, "post_setup.sql"),
        )
    return (pre_setup, setup, post_setup)


def _load_setup_sql(sql_file_path: str):
    try:
        with open(sql_file_path, "r") as file:
            sql_content = file.read()
        sql_commands = [cmd.strip() for cmd in sql_content.split(";") if cmd.strip()]
        return sql_commands
    except Exception as e:
        return []


def config_to_df(
    job_id: str,
    run_time: datetime.datetime,
    experiment_config: dict,
    model_config: dict,
    db_configs: list[dict],
):
    configs = []
    config = {
        "experiment_config": experiment_config,
        "model_config": model_config,
        "db_configs": db_configs,
    }
    df = pd.json_normalize(config, sep=".")
    d_flat = df.to_dict(orient="records")[0]
    for key in d_flat:
        configs.append(
            {
                "job_id": job_id,
                "run_time": run_time,
                "config": key,
                "value": d_flat[key],
            }
        )
    df = pd.DataFrame.from_dict(configs)
    df[["job_id", "config", "value"]] = df[["job_id", "config", "value"]].astype(
        "string"
    )
    return df


def update_google3_relative_paths(experiment_config: dict, session_id: str):
    if isinstance(experiment_config, dict):
        for key, value in experiment_config.items():
            if isinstance(value, dict):
                update_google3_relative_paths(value, session_id)
            elif isinstance(value, list):
                values = []
                for sub_value in value:
                    if isinstance(sub_value, str) and sub_value.startswith("google3/"):
                        values.append(get_google3_relative_path(sub_value, session_id))
                    else:
                        values.append(sub_value)
                experiment_config[key] = values
            elif isinstance(value, str) and value.startswith("google3/"):
                experiment_config[key] = get_google3_relative_path(
                    experiment_config[key], session_id
                )


def get_google3_relative_path(value, session_id):
    return os.path.join(
        SESSION_RESOURCES_PATH,
        session_id,
        value,
    )


def set_session_configs(session, experiment_config: dict):
    session["config"] = experiment_config
    if "dataset_config" in experiment_config and experiment_config["dataset_config"]:
        session["dataset_config"] = experiment_config["dataset_config"]
    if (
        "database_configs" in experiment_config
        and experiment_config["database_configs"]
        and len(experiment_config["database_configs"])
    ):
        session["db_configs"] = breakdown_db_configs_by_dialect(
            experiment_config["database_configs"]
        )
    else:
        session["db_configs"] = []
    if "model_config" in experiment_config and experiment_config["model_config"]:
        session["model_config"] = load_yaml_config(experiment_config["model_config"])
    session["setup_config"] = {}
    if "setup_directory" in experiment_config and experiment_config["setup_directory"]:
        session["setup_config"]["setup_directory"] = experiment_config[
            "setup_directory"
        ]


def generate_key(length=12):
    categories = [string.ascii_lowercase, string.digits, "_"]
    key = [random.choice(c) for c in random.sample(categories, 3)]
    key += random.choices("".join(categories), k=length - 3)
    return "".join(key)


def breakdown_db_configs_by_dialect(db_configs: list[dict]):
    db_configs_by_dialect = {}
    for db_config_yaml in db_configs:
        db_config = load_yaml_config(db_config_yaml)
        dialect = db_config.get("dialect")
        if not dict:
            continue
        elif dialect in db_configs_by_dialect:
            db_configs_by_dialect[dialect].append(db_config)
        else:
            db_configs_by_dialect[dialect] = [db_config]
    return db_configs_by_dialect
