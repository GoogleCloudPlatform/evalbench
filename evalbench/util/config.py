import datetime
import logging
import os
import csv
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
                    

def config_to_df(
    job_id: str,
    run_time: datetime.datetime,
    experiment_config: dict,
    model_config: dict,
    db_config: dict,
):
    configs = []
    config = {
        "experiment_config": experiment_config,
        "model_config": model_config,
        "db_config": db_config,
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
            elif isinstance(value, str) and value.startswith("google3/"):
                updated_path = os.path.join(
                    SESSION_RESOURCES_PATH,
                    session_id,
                    experiment_config[key],
                )
                experiment_config[key] = updated_path
