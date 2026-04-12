from absl import flags

EXPERIMENT_CONFIG = flags.DEFINE_string(
    "experiment_config",
    "configs/experiment_config.yaml",
    "Path to the eval execution configuration file.",
)
