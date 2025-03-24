from .gemini import GeminiGenerator
from .passthrough import NOOPGenerator
from .claude import ClaudeGenerator


def get_generator(model_config):
    if model_config["generator"] == "gcp_vertex_gemini":
        return GeminiGenerator(model_config)
    if model_config["generator"] == "gcp_vertex_claude":
        return ClaudeGenerator(model_config)
    if model_config["generator"] == "noop":
        return NOOPGenerator(model_config)
    else:
        raise ValueError(f"Unknown Generator {model_config['generator']}")
