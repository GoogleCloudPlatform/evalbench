from .sqlgenbase import SQLGenBasePromptGenerator
from .passthrough import NOOPGenerator
from .interactsystem import InteractSystemGenerator
from .interactuser import InteractUserGenerator
from .simulateduser import SimulatedUserPromptGenerator
from .dataagentinteractuser import DataAgentInteractUserGenerator


def get_generator(db, promptgenerator_config, generator_name=None):
    if generator_name:
        promptgenerator_config["prompt_generator"] = generator_name

    if promptgenerator_config["prompt_generator"] == "SQLGenBasePromptGenerator":
        return SQLGenBasePromptGenerator(db, promptgenerator_config)
    if promptgenerator_config["prompt_generator"] == "NOOPGenerator":
        return NOOPGenerator(None, promptgenerator_config)
    if promptgenerator_config["prompt_generator"] == "InteractSystemGenerator":
        return InteractSystemGenerator(db, promptgenerator_config)
    if promptgenerator_config["prompt_generator"] == "InteractUserGenerator":
        return InteractUserGenerator(db, promptgenerator_config)
    if promptgenerator_config["prompt_generator"] == "SimulatedUserPromptGenerator":
        return SimulatedUserPromptGenerator(db, promptgenerator_config)
    if promptgenerator_config["prompt_generator"] == "DataAgentInteractUserGenerator":
        return DataAgentInteractUserGenerator(db, promptgenerator_config)
    raise ValueError("Prompt Generator not Supported")
