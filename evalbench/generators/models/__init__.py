from .alloydb_ai_nl import AlloyDBGenerator
from databases import DB
from generators.models.generator import QueryGenerator
from .gemini import GeminiGenerator
from .passthrough import NOOPGenerator
from .claude import ClaudeGenerator
from .querydata import QueryData
from util.config import load_yaml_config


def get_generator(global_models, model_config_path: str, db: DB = None):
    with global_models.get("lock"):
        global_model_configs = global_models.get("registered_models")
        if model_config_path in global_model_configs:
            return global_model_configs[model_config_path]

        config = load_yaml_config(model_config_path)
        # Create a new model_config
        model: QueryGenerator | None = None
        if config["generator"] == "gcp_vertex_gemini":
            model = GeminiGenerator(config)
        if config["generator"] == "gcp_vertex_claude":
            model = ClaudeGenerator(config)
        if config["generator"] == "noop":
            model = NOOPGenerator(config)
        if config["generator"] == "alloydb_ai_nl":
            model = AlloyDBGenerator(db, config)
        if config["generator"] == "querydata":
            model = QueryData(config)
        if not model:
            raise ValueError(f"Unknown Generator {config['generator']}")

        global_model_configs[model_config_path] = model
    return model
