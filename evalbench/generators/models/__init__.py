import importlib

from .alloydb_ai_nl import AlloyDBGenerator
from databases import DB
from generators.models.generator import QueryGenerator
from .gemini import GeminiGenerator
from .passthrough import NOOPGenerator
from .claude import ClaudeGenerator
from .querydata import QueryData
from .query_data_api import QueryDataAPIGenerator
from .gemini_cli import GeminiCliGenerator
from .claude_code import ClaudeCodeGenerator
from .codex_cli import CodexCliGenerator
from .gcp_data_engineering_agent import DataEngineeringAgentGenerator
from .agy_cli import AgyCliGenerator
from .mcp_tools import McpToolsGenerator
from .noop_agent import NoopAgentGenerator
from .agent_runtime import AgentRuntimeGenerator
from util.config import load_yaml_config


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


def _get_grpc_proxy(config):
    from .grpc_proxy import GrpcProxyModel
    return GrpcProxyModel(config)


def _get_agent_grpc_proxy(config):
    from .agent_grpc_proxy import AgentGrpcProxyGenerator
    return AgentGrpcProxyGenerator(config)


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
            "grpc_proxy": lambda: _get_grpc_proxy(config),
            "agent_grpc_proxy": lambda: _get_agent_grpc_proxy(config),
            "gemini_cli": lambda: GeminiCliGenerator(config),
            "claude_code": lambda: ClaudeCodeGenerator(config),
            "codex_cli": lambda: CodexCliGenerator(config),
            "data_engineering_agent": lambda: DataEngineeringAgentGenerator(config),
            "agy_cli": lambda: AgyCliGenerator(config),
            "mcp_tools": lambda: McpToolsGenerator(config),
            "noop_agent": lambda: NoopAgentGenerator(config),
            "agent_runtime": lambda: AgentRuntimeGenerator(config),
        }
        generator = config.get("generator")
        if "generator_class" in config:
            gen_cls = _load_custom_class(config["generator_class"])
            model = gen_cls(config)
        elif generator == "custom":
            raise ValueError(
                "generator 'custom' specified, but 'generator_class' is missing from"
                " model config."
            )
        elif generator not in generators:
            raise ValueError(f"Unknown Generator {generator}")
        else:
            model = generators[generator]()

        global_model_configs[model_config_path] = model
    return model
