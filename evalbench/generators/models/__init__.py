from .gemini import GeminiGenerator
from .passthrough import NOOPGenerator
from .claude import ClaudeGenerator


def get_generator(model_config):
    if model_config["generator"] == "gemini":
        return GeminiGenerator(model_config)
    if model_config["generator"] == "noop":
        return NOOPGenerator(model_config)
    if model_config["generator"] == "claude":
        return ClaudeGenerator(model_config)
