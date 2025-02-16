from .gemini import GeminiGenerator
from .magick import MagicSQLGenerator
from .passthrough import NOOPGenerator


def get_generator(core_db, model_config):
    if model_config["generator"] == "gemini":
        return GeminiGenerator(core_db, model_config)
    if model_config["generator"] == "magick":
        return MagicSQLGenerator(core_db, model_config)
    if model_config["generator"] == "noop":
        return NOOPGenerator(core_db, model_config)
