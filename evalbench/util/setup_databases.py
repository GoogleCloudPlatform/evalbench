"""
Utility script to setup databases (schema and data) for an EvalBench experiment natively.

This script parses a standard EvalBench experiment_config YAML file (the same file
you would pass to evalbench.py), extracts the database requirements and setup paths,
and automatically creates and sets up the schemas in the target engines.

Example usage:
  python3 evalbench/util/setup_databases.py --experiment_config datasets/bird/example_run_config.yaml
"""


from evalbench.evaluator.db_manager import _get_setup_values
from evalbench.databases import get_database
from evalbench.dataset.dataset import load_dataset_from_json, flatten_dataset
from evalbench.util.config import load_yaml_config
import sys
import os
from absl import app
from absl import flags

# Ensure evalbench is in sys.path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', '..'))


def setup_databases(config_path: str):
    config = load_yaml_config(config_path)

    # Load dataset to figure out what DBs are needed
    dataset = load_dataset_from_json(config["dataset_config"], config)
    dataset = flatten_dataset(dataset)

    # Get unique databases (db_ids) needed for this experiment
    unique_db_names = set(item.database for item in dataset)

    print(f"Setting up databases for: {unique_db_names}")

    for db_config_path in config.get("database_configs", []):
        db_config = load_yaml_config(db_config_path)
        db_type = db_config["db_type"]

        for db_name in unique_db_names:
            print(f"Processing {db_name} for engine {db_type}...")

            # Get connection wrapper to the specific database
            core_db = get_database(db_config, db_name)

            # Ensure the permanent database exists BEFORE running resetup_database
            core_db.ensure_database_exists(db_name)

            # Load setup scripts natively from SQL directory
            setup_config = config
            try:
                setup_scripts, data = _get_setup_values(
                    setup_config, db_name, db_type)
            except Exception as e:
                print(f"  Failed to load setup values: {e}")
                continue

            core_db.set_setup_instructions(setup_scripts, data)

            # Execute setup!
            try:
                core_db.resetup_database(force=True, setup_users=False)
                print(f"  Successfully instantiated {db_name} on {db_type}")
            except Exception as e:
                print(f"  Failed to setup {db_name} on {db_type}: {e}")


_EXPERIMENT_CONFIG = flags.DEFINE_string(
    "experiment_config",
    None,
    "Path to the eval execution configuration file.",
    required=True
)


def main(argv):
    if len(argv) > 1:
        raise app.UsageError("Too many command-line arguments.")
    setup_databases(_EXPERIMENT_CONFIG.value)


if __name__ == "__main__":
    app.run(main)
