import importlib

from .postgres import PGDB
from .mysql import MySQLDB
from .sqlserver import SQLServerDB
from .sqlite import SQLiteDB
from .db import DB
from .bigquery import BQDB
from .bigtable import BigtableDB
from .alloydb import AlloyDB
from .alloydb_omni import AlloyDBOmni
from .spanner import SpannerDB
from .mongodb import MongoDB


def _load_custom_class(class_path: str):
    """Dynamically imports and returns a class from a module path."""
    if ":" in class_path:
        mod_name, cls_name = class_path.split(":", 1)
    elif "." in class_path:
        mod_name, cls_name = class_path.rsplit(".", 1)
    else:
        raise ValueError(
            f"Invalid class_path '{class_path}'. Expected format"
            " 'module.submodule.ClassName' or 'module:ClassName'."
        )

    try:
        mod = importlib.import_module(mod_name)
    except ImportError as e:
        raise ImportError(
            f"Failed to import module '{mod_name}' for custom class: {e}"
        ) from e

    if not hasattr(mod, cls_name):
        raise AttributeError(
            f"Module '{mod_name}' has no attribute or class '{cls_name}'."
        )

    return getattr(mod, cls_name)


def get_database(db_config, db_name) -> DB:
    # if db_name is provided:
    #   - It will override the provided default database_name
    #   - This is useful as the default db may be "postgres" or a default only used for setup
    if db_name:
        suffix = db_config.get("db_name_suffix", "")
        db_config["database_name"] = f"{db_name}{suffix}"

    if "connector_class" in db_config:
        cls = _load_custom_class(db_config["connector_class"])
        return cls(db_config)
    if db_config.get("db_type") == "custom":
        raise ValueError(
            "db_type 'custom' specified, but 'connector_class' is missing from"
            " db_config."
        )

    if db_config["db_type"] == "postgres":
        return PGDB(db_config)
    if db_config["db_type"] == "spanner":
        return SpannerDB(db_config)
    if db_config["db_type"] == "mysql":
        return MySQLDB(db_config)
    if db_config["db_type"] == "sqlserver":
        return SQLServerDB(db_config)
    if db_config["db_type"] == "sqlite":
        return SQLiteDB(db_config)
    if db_config["db_type"] == "bigquery":
        return BQDB(db_config)
    if db_config["db_type"] == "alloydb":
        return AlloyDB(db_config)
    if db_config["db_type"] == "alloydb_omni":
        return AlloyDBOmni(db_config)
    if db_config["db_type"] == "bigtable":
        return BigtableDB(db_config)
    if db_config["db_type"] == "mongodb":
        return MongoDB(db_config)
    raise ValueError("DB Type not Supported")
