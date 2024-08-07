from .gemini import GeminiGenerator
from .magick import MagicSQLGenerator
from .passthrough import NOOPGenerator


def get_generator(model_config):
    if model_config["generator"] == "gemini":
        return GeminiGenerator(model_config)
    if model_config["generator"] == "magick":
        return MagicSQLGenerator(model_config)
    if model_config["generator"] == "noop":
        return NOOPGenerator(model_config)
