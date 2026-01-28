from dataset import evalinput


def load_session_configs(session):
    return (
        session["config"] if "config" in session else None,
        session["db_configs"] if "db_configs" in session else None,
        session["model_config"] if "model_config" in session else None,
        session["setup_config"] if "setup_config" in session else None,
    )


async def get_dataset_from_request(request_iterator):
    return [
        evalinput.EvalInputRequest.init_from_proto(request)
        async for request in request_iterator
    ]
