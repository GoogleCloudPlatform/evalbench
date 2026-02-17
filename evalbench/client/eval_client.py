import asyncio
from aiologger import Logger
import grpc
from evalproto import eval_request_pb2, eval_connect_pb2, eval_config_pb2
from evalproto import eval_service_pb2_grpc
import random
import argparse
import google.oauth2.id_token
import google.auth.transport.requests


def get_id_token(audience):
    """
    Fetches an ID token for the specified audience.
    """
    # The request object is used to make the HTTP call to fetch the token
    request = google.auth.transport.requests.Request()

    # This will search for credentials in:
    # 1. Environment variables (GOOGLE_APPLICATION_CREDENTIALS)
    # 2. Metadata server (if running on GCP)
    token = google.oauth2.id_token.fetch_id_token(request, audience)

    return token


class EvalbenchClient:
    def __init__(self, endpoint: str):
        self.endpoint = endpoint
        if self.endpoint == "local":
            address = "localhost:50051"
            channel_creds = grpc.alts_channel_credentials()
            self.channel = grpc.aio.secure_channel(address, channel_creds)
        else:
            address = f"{self.endpoint}:443"
            id_token = get_id_token(f"https://{self.endpoint}")
            # 2. Create Call Credentialss
            # This puts the token in the 'authorization: Bearer <token>' header
            call_creds = grpc.access_token_call_credentials(id_token)

            # 3. Create SSL Channel Credentials
            # ID tokens require a secure (TLS) channel
            channel_creds = grpc.ssl_channel_credentials()

            # 4. Composite Credentials
            # Combine the SSL channel with the token-based call credentials
            composite_creds = grpc.composite_channel_credentials(channel_creds, call_creds)
            self.channel = grpc.aio.secure_channel(address, composite_creds)

        self.stub = eval_service_pb2_grpc.EvalServiceStub(self.channel)
        rpc_id = "{:032x}".format(random.getrandbits(128))
        self.metadata = grpc.aio.Metadata(
            ("client-rpc-id", rpc_id),
        )

    async def ping(self):
        request = eval_request_pb2.PingRequest()
        response = await self.stub.Ping(request, metadata=self.metadata)
        return response

    async def connect(self):
        request = eval_connect_pb2.EvalConnectRequest()
        request.client_id = "me"
        response = await self.stub.Connect(request, metadata=self.metadata)
        return response

    async def set_evalconfig(self, experiment: str):
        data = None
        with open(experiment, "rb") as f:
            data = f.read()
        request = eval_config_pb2.EvalConfigRequest()
        request.yaml_config = data
        response = await self.stub.EvalConfig(request, metadata=self.metadata)
        return response

    async def get_evalinputs(self):
        request = eval_request_pb2.EvalInputRequest()
        get_evalinputs_stream = self.stub.ListEvalInputs(
            request, metadata=self.metadata
        )
        while True:
            response = await get_evalinputs_stream.read()
            if response == grpc.aio.EOF:
                break
            yield response

    async def eval(self, evalinputs):
        eval_call = self.stub.Eval(metadata=self.metadata)
        for eval_input in evalinputs:
            await eval_call.write(eval_input)
        await eval_call.done_writing()
        response = await eval_call
        return response


async def run(experiment: str, endpoint: str) -> None:
    logger = Logger.with_default_handlers(name="evalbench-logger")
    evalbenchclient = EvalbenchClient(endpoint)
    response = await evalbenchclient.ping()
    logger.info(f"ping Returned: {response.response}")

    response = await evalbenchclient.connect()
    logger.info(f"connect Returned: {response.response}")

    response = await evalbenchclient.ping()
    logger.info(f"ping Returned: {response.response}")

    response = await evalbenchclient.set_evalconfig(experiment)
    logger.info(f"get_evalinput Returned: {response.response}")

    evalInputs = []
    async for response in evalbenchclient.get_evalinputs():
        evalInputs.append(response)
    logger.info(f"evalInputs: {len(evalInputs)}")
    response = await evalbenchclient.eval(evalInputs)
    logger.info(f"eval Returned: {response.response}")


async def main():
    logger = Logger.with_default_handlers(name="evalbench-logger")
    parser = argparse.ArgumentParser()
    parser.add_argument("--experiment", dest="experiment")
    parser.add_argument("--endpoint", dest="endpoint")
    known_args, _ = parser.parse_known_args()

    await run(known_args.experiment, known_args.endpoint)
    # get a set of all running tasks
    all_tasks = asyncio.all_tasks()
    # get the current tasks
    current_task = asyncio.current_task()
    # remove the current task from the list of all tasks
    all_tasks.remove(current_task)
    # report a message
    print(f"Main waiting for {len(all_tasks)} tasks...")
    # suspend until all tasks are completed
    if len(all_tasks) > 0:
        await asyncio.wait(all_tasks)
    await logger.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
