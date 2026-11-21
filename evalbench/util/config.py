import json
import datetime
import logging
import os
import csv
import random
import re
import string
from typing import List, Optional

from pyaml_env import parse_config
import pandas as pd
from .sessionmgr import SESSION_RESOURCES_PATH
from google.protobuf import text_format

logging.getLogger().setLevel(logging.INFO)


def load_yaml_config(yaml_file):
    current_directory = os.getcwd()
    config = parse_config(os.path.join(current_directory, yaml_file))
    return config


def load_textproto(textproto_file, text_proto_object):
    with open(textproto_file, "r") as file:
        text_format.Merge(file.read(), text_proto_object)
    return text_proto_object


def load_db_data_from_csvs(data_directory: str):
    tables: dict[str, List[str]] = {}
    current_directory = os.getcwd()
    if not os.path.isdir(os.path.join(current_directory, data_directory)):
        return tables
    for filename in os.listdir(
        os.path.join(
            current_directory,
            data_directory)):
        if filename.endswith(".csv"):
            table_name = filename[:-4]
            with open(
                os.path.join(current_directory, data_directory, filename), "r"
            ) as csvfile:
                reader = csv.reader(csvfile, quotechar='"')
                rows = []
                for row in reader:
                    rows.append(row)
                tables[table_name] = rows
    return tables


def load_setup_scripts(setup_scripts_directory_path: str):
    current_directory = os.getcwd()
    pre_setup = _load_setup_sql(
        os.path.join(current_directory,
                     setup_scripts_directory_path, "pre_setup.sql"),
    )
    setup = _load_setup_sql(
        os.path.join(current_directory,
                     setup_scripts_directory_path, "setup.sql"),
    )
    # Check for setup.json and append it if exists
    setup_json_path = os.path.join(
        current_directory, setup_scripts_directory_path, "setup.json"
    )
    if os.path.exists(setup_json_path):
        with open(setup_json_path, "r") as f:
            setup.append(f.read())

    # Check for post_setup.json
    post_setup_json_path = os.path.join(
        current_directory, setup_scripts_directory_path, "post_setup.json"
    )
    if os.path.exists(post_setup_json_path):

        with open(post_setup_json_path, "r") as f:
            # Load as list of dicts, then convert back to strings for
            # batch_execute
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
            os.path.join(
                current_directory,
                setup_scripts_directory_path,
                "post_setup.sql"),
        )
    return (pre_setup, setup, post_setup)


def _load_setup_sql(sql_file_path: str):
    try:
        with open(sql_file_path, "r") as file:
            sql_content = file.read()
        sql_commands = [cmd.strip()
                        for cmd in sql_content.split(";") if cmd.strip()]
        return sql_commands
    except Exception as e:
        return []


REDACTED = "[REDACTED]"
_SECRET_KEY_WORDS = frozenset({
    "password", "passwd", "secret", "token", "credential", "credentials",
    "authorization", "apikey",
})
# "key" is a secret only after one of these words, as in api_key.
_SECRET_KEY_QUALIFIERS = frozenset({"api", "private", "access", "secret"})
_CAMEL_CASE_RE = re.compile(r"([a-z0-9])([A-Z])")
_URL_PASSWORD_RE = re.compile(r"(://[^/\s:@]+:)[^@\s/]+(@)")
# A Secret Manager resource name is a reference to a secret, not a secret.
_SECRET_REFERENCE_RE = re.compile(
    r"^projects/[^/\s]+/secrets/[^/\s]+(/versions/[^/\s]+)?$")


def _is_secret_key(key) -> bool:
    """Returns True if the last words of a config key name a secret."""
    words = [
        word
        for word in re.split(
            r"[^a-z0-9]+", _CAMEL_CASE_RE.sub(r"\1_\2", str(key)).lower())
        if word
    ]
    if not words:
        return False
    if words[-1] in _SECRET_KEY_WORDS:
        return True
    return (
        len(words) > 1
        and words[-1] == "key"
        and words[-2] in _SECRET_KEY_QUALIFIERS
    )


def _is_secret_value(value) -> bool:
    if value is None or value == "" or isinstance(value, bool):
        return False
    return not (isinstance(value, str) and _SECRET_REFERENCE_RE.match(value))


def redact_secrets(config):
    """Returns a copy of a config without the values of secret keys.

    The copy is for reports (configs.csv, BigQuery). It also removes the
    password from a URL of the form scheme://user:password@host.
    """
    if isinstance(config, dict):
        return {
            key: REDACTED
            if _is_secret_key(key) and _is_secret_value(value)
            else redact_secrets(value)
            for key, value in config.items()
        }
    if isinstance(config, (list, tuple)):
        return [redact_secrets(item) for item in config]
    if isinstance(config, str):
        return _URL_PASSWORD_RE.sub(
            lambda match: match.group(1) + REDACTED + match.group(2), config)
    return config


def config_to_df(
    job_id: str,
    run_time: datetime.datetime,
    experiment_config: dict,
    model_config: dict,
    db_configs: list[dict],
):
    configs = []
    config = redact_secrets({
        "experiment_config": experiment_config,
        "model_config": model_config,
        "db_configs": db_configs,
    })
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
    df[["job_id", "config", "value"]] = df[[
        "job_id", "config", "value"]].astype("string")
    return df


def with_agent_version(model_config: dict, orchestrator) -> dict:
    """Returns model_config plus the orchestrator's agent_version, if set."""
    version = getattr(orchestrator, "agent_version", None)
    if not isinstance(version, str) or not version:
        return model_config
    return {**(model_config or {}), "agent_version": version}


def update_google3_relative_paths(
    experiment_config: dict, session_id: str, resource_map: dict
):
    if isinstance(experiment_config, dict):
        for key, value in experiment_config.items():
            if isinstance(value, dict):
                update_google3_relative_paths(value, session_id, resource_map)
            elif isinstance(value, list):
                values = []
                for sub_value in value:
                    if isinstance(sub_value,
                                  str) and sub_value.startswith("google3/"):
                        values.append(get_google3_relative_path(
                            sub_value, session_id))
                    elif isinstance(sub_value, str) and sub_value in resource_map:
                        values.append(
                            os.path.join(
                                SESSION_RESOURCES_PATH,
                                session_id,
                                resource_map[sub_value],
                            )
                        )
                    elif isinstance(sub_value, dict):
                        for k, v in sub_value.items():
                            if isinstance(v, str) and v.startswith("google3/"):
                                sub_value[k] = get_google3_relative_path(
                                    v, session_id
                                )
                        values.append(sub_value)
                    else:
                        values.append(sub_value)
                experiment_config[key] = values
            elif isinstance(value, str) and value.startswith("google3/"):
                experiment_config[key] = get_google3_relative_path(
                    experiment_config[key], session_id
                )
            elif isinstance(value, str) and value in resource_map:
                experiment_config[key] = os.path.join(
                    SESSION_RESOURCES_PATH,
                    session_id,
                    resource_map[value],
                )


def get_google3_relative_path(value, session_id):
    return os.path.join(
        SESSION_RESOURCES_PATH,
        session_id,
        value,
    )


def set_session_configs(session, experiment_config: dict):
    session["config"] = experiment_config
    # Always set dataset_config so callers can subscript it unconditionally. Some
    # orchestrators (e.g. mcp_readability) drive their work from the run config
    # itself and have no prompt dataset, in which case this stays None and
    # load_dataset_from_json returns {}.
    session["dataset_config"] = None
    if "dataset_config" in experiment_config and experiment_config["dataset_config"]:
        # Handle both flat string paths and nested dicts (e.g. BIRD configs)
        dc = experiment_config["dataset_config"]
        if isinstance(dc, dict) and "prompts_file" in dc:
            session["dataset_config"] = dc["prompts_file"]
        else:
            session["dataset_config"] = dc
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
        session["model_config"] = load_yaml_config(
            experiment_config["model_config"])
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
        if not db_config:
            continue
        dialect = db_config.get("dialect")
        if dialect in db_configs_by_dialect:
            db_configs_by_dialect[dialect].append(db_config)
        else:
            db_configs_by_dialect[dialect] = [db_config]
    return db_configs_by_dialect


def parse_timeout_seconds(val) -> Optional[float]:
    """Parses a timeout duration (int, float, or string like '300', '5m', '1h', '500ms') into seconds as float.

    Returns None if val is None or empty string.
    Raises ValueError if val is negative or has an invalid format string.
    Raises TypeError if val is not int, float, str, or None.
    """
    if val is None:
        return None
    if isinstance(val, bool):
        raise TypeError(f"Timeout cannot be a boolean: {val}")
    if isinstance(val, (int, float)):
        if val < 0:
            raise ValueError(f"Timeout cannot be negative: {val}")
        return float(val)
    if not isinstance(val, str):
        raise TypeError(
            f"Timeout must be int, float, str, or None, got {type(val).__name__}"
        )

    s = val.strip()
    if not s:
        return None

    try:
        f = float(s)
        if f < 0:
            raise ValueError(f"Timeout cannot be negative: {val}")
        return f
    except ValueError:
        pass

    matches = list(re.finditer(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h|d)", s))
    if not matches:
        raise ValueError(f"Invalid timeout format: '{val}'")

    matched_len = sum(len(m.group(1)) + len(m.group(2)) for m in matches)
    if matched_len != len(re.sub(r"\s+", "", s)):
        raise ValueError(f"Invalid timeout format: '{val}'")

    unit_multipliers = {
        "ms": 0.001,
        "s": 1.0,
        "m": 60.0,
        "h": 3600.0,
        "d": 86400.0,
    }

    total = 0.0
    for m in matches:
        amount = float(m.group(1))
        unit = m.group(2)
        total += amount * unit_multipliers[unit]

    if total < 0:
        raise ValueError(f"Timeout cannot be negative: {val}")

    return total


def get_eval_case_timeout(config: dict) -> Optional[float]:
    """Extracts and parses eval_case_timeout setting from run config or scenario."""
    if not isinstance(config, dict):
        return None

    val = config.get("eval_case_timeout")
    return parse_timeout_seconds(val) if val is not None else None
