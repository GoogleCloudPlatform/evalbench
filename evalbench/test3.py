import databases
import util.config as util_config
import setup_teardown

config_path = "../datasets/nld/google/bat/db_configs/db_blog/csql_mysql.yaml"
config = util_config.load_yaml_config(config_path)

experiment_config_path = "configs/newDatasetFormat.yaml"
experiment_config = util_config.load_yaml_config(experiment_config_path)

setup_teardown.setupDatabase(db_config=config, experiment_config=experiment_config, database="db_blog", create_user=True)