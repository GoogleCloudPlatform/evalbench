import generators.models as models
import generators.prompts as prompts
import threading
import logging


class SimulatedUser:
    def __init__(self, config):
        self.config = config

        global_models = {
            "lock": threading.Lock(),
            "registered_models": {}
        }

        # Expect 'simulated_user_model_config' path in config
        model_config_path = config.get("simulated_user_model_config")

        self.prompt_generator = prompts.get_generator(
            None,
            {"prompt_generator": "SimulatedUserPromptGenerator"},
            "SimulatedUserPromptGenerator"
        )

        self.model_generator = None
        if model_config_path:
            try:
                self.model_generator = models.get_generator(
                    global_models, model_config_path, None
                )
            except Exception as e:
                logging.warning(f"Failed to load simulated user model from {model_config_path}: {e}")
        else:
            logging.warning("No 'simulated_user_model_config' provided. SimulatedUser will not be able to generate responses.")

    def get_next_response(self, conversation_plan: str, history: list, last_agent_reply: str) -> str:
        if not self.model_generator:
            logging.error("Model generator not initialized.")
            return "TERMINATE"

        payload = {
            "conversation_plan": conversation_plan,
            "history": history,
            "last_agent_reply": last_agent_reply
        }

        # Generate prompt
        self.prompt_generator.generate(payload)
        prompt = payload["prompt"]

        # Call model
        response = self.model_generator.generate(prompt)
        return response
