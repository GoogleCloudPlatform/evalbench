from dataset import evalinput
import databases
import generators.models as models
import generators.prompts as prompts


def load_session_configs(session):
    return (
        session["config"],
        session["db_config"],
        session["model_config"],
        session["setup_config"],
    )


def create_eval_instances(config, db_config, model_config):
    core_db = databases.get_database(db_config)
    model_generator = models.get_generator(model_config)
    prompt_generator = prompts.get_generator(core_db, config)
    return core_db, model_generator, prompt_generator


async def get_dataset_from_request(request_iterator):
    return [
        evalinput.EvalInputRequest.init_from_proto(request)
        async for request in request_iterator
    ]
