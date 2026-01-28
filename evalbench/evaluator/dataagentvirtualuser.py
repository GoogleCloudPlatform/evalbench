import generators.models as models
import generators.prompts as prompts
from dataset.evalinteractinput import InteractionType


class DataAgentVUser:
    def __init__(self, config, global_models, core_db):
        self.config = config
        self.prompt_generator = prompts.get_generator(
            core_db, self.config, "DataAgentInteractUserGenerator"
        )

        self.model_generator = models.get_generator(
            global_models, self.config["vuser_model_config"], None
        )

    def disambiguate(self, eval_output: dict) -> str:
        item = eval_output["payload"]
        turn_i = item["turn"]
        eval_output["step_type"] = InteractionType.VUSER_ENCODE
        self.prompt_generator.generate(eval_output)
        generated = self.model_generator.generate(item["prompt"])
        item[f"user_encoded_answer_{turn_i}"] = generated

        eval_output["step_type"] = InteractionType.VUSER_DECODE
        self.prompt_generator.generate(eval_output)
        generated = self.model_generator.generate(item["prompt"])
        item[f"user_decoded_answer_{turn_i}"] = generated
        item[f"user_answer_{turn_i}"] = generated
        eval_output["step_type"] = InteractionType.DISAMBIGUATE
        return generated
