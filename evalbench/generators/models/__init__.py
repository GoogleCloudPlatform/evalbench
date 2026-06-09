from .alloydb_ai_nl import AlloyDBGenerator
from databases import DB
from generators.models.generator import QueryGenerator
from .gemini import GeminiGenerator
from .passthrough import NOOPGenerator
from .grpc_proxy import GrpcProxyModel
from .claude import ClaudeGenerator
from .querydata import QueryData
from .query_data_api import QueryDataAPIGenerator
from .gemini_cli import GeminiCliGenerator
from .claude_code import ClaudeCodeGenerator
from .codex_cli import CodexCliGenerator
from .gcp_data_engineering_agent import DataEngineeringAgentGenerator
from .agy_cli import AgyCliGenerator
from util.config import load_yaml_config


def get_generator(global_models, model_config_path: str, db: DB = None):
    with global_models.get("lock"):
        global_model_configs = global_models.get("registered_models")
        if model_config_path in global_model_configs:
            return global_model_configs[model_config_path]

        config = load_yaml_config(model_config_path)
        # Create a new model_config
        generators = {
            "gcp_vertex_gemini": lambda: GeminiGenerator(config),
            "gcp_vertex_claude": lambda: ClaudeGenerator(config),
            "noop": lambda: NOOPGenerator(config),
            "alloydb_ai_nl": lambda: AlloyDBGenerator(db, config),
            "querydata": lambda: QueryData(config),
            "query_data_api": lambda: QueryDataAPIGenerator(config),
            "grpc_proxy": lambda: GrpcProxyModel(config),
            "gemini_cli": lambda: GeminiCliGenerator(config),
            "claude_code": lambda: ClaudeCodeGenerator(config),
            "codex_cli": lambda: CodexCliGenerator(config),
            "data_engineering_agent": lambda: DataEngineeringAgentGenerator(config),
            "agy_cli": lambda: AgyCliGenerator(config),
        }
        generator = config["generator"]
        if generator not in generators:
            raise ValueError(f"Unknown Generator {generator}")
        model = generators[generator]()

        global_model_configs[model_config_path] = model
    return model
