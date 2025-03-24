"""A gRPC servicer that handles EvalService requests."""

from collections.abc import AsyncIterator
from typing import AsyncGenerator

from absl import logging
from typing import Awaitable, Callable, Optional
import contextvars
import yaml
import grpc
import pathlib
from dataset.dataset import load_json
import reporting.report as report
from reporting import get_reporters
import reporting.analyzer as analyzer
from util.config import update_google3_relative_paths, set_session_configs, config_to_df
from util import get_SessionManager
from dataset.dataset import load_dataset_from_json
from evaluator.evaluator import Evaluator
from evalproto import (
    eval_request_pb2,
    eval_response_pb2,
    eval_service_pb2_grpc,
)
from util.service import (
    load_session_configs,
    get_dataset_from_request,
    create_eval_instances,
)


SESSIONMANAGER = get_SessionManager()
rpc_id_var = contextvars.ContextVar("rpc_id", default="default")


class SessionManagerInterceptor(grpc.aio.ServerInterceptor):
    def __init__(self, tag: str, rpc_id: Optional[str] = None) -> None:
        self.tag = tag
        self.rpc_id = rpc_id

    async def intercept_service(
        self,
        continuation: Callable[
            [grpc.HandlerCallDetails], Awaitable[grpc.RpcMethodHandler]
        ],
        handler_call_details: grpc.HandlerCallDetails,
    ) -> grpc.RpcMethodHandler:
        _metadata = dict(handler_call_details.invocation_metadata)  # type: ignore
        if rpc_id_var.get() == "default":
            _metadata = dict(handler_call_details.invocation_metadata)  # type: ignore
            rpc_id_var.set(self.decorate(_metadata["client-rpc-id"]))
            SESSIONMANAGER.create_session(rpc_id_var.get())
        else:
            rpc_id_var.set(self.decorate(rpc_id_var.get()))
        return await continuation(handler_call_details)

    def decorate(self, rpc_id: str):
        return f"{self.tag}-{rpc_id}"


class EvalServicer(eval_service_pb2_grpc.EvalServiceServicer):
    """A gRPC servicer that handles EvalService requests."""

    def __init__(self) -> None:
        super().__init__()
        logging.info("EvalBench v1.0.0")

    async def Ping(
        self,
        request: eval_request_pb2.PingRequest,
        context: grpc.ServicerContext,
    ) -> eval_response_pb2.EvalResponse:
        return eval_response_pb2.EvalResponse(response=f"ack")

    async def Connect(
        self,
        request,
        context,
    ) -> eval_response_pb2.EvalResponse:
        return eval_response_pb2.EvalResponse(response=f"ack")

    async def EvalConfig(
        self,
        request,
        context,
    ) -> eval_response_pb2.EvalResponse:
        experiment_config = yaml.safe_load(request.yaml_config.decode("utf-8"))
        session = SESSIONMANAGER.get_session(rpc_id_var.get())
        SESSIONMANAGER.write_resource_files(rpc_id_var.get(), request.resources)
        update_google3_relative_paths(experiment_config, rpc_id_var.get())
        set_session_configs(session, experiment_config)
        return eval_response_pb2.EvalResponse(response=f"ack")

    async def ListEvalInputs(
        self,
        request,
        context,
    ) -> AsyncGenerator[eval_request_pb2.EvalInputRequest, None]:
        session = SESSIONMANAGER.get_session(rpc_id_var.get())
        logging.info("Retrieving Evals for: %s.", rpc_id_var.get())
        experiment_config = session["config"]
        dataset_config_json = experiment_config["dataset_config"]
        dataset = load_dataset_from_json(dataset_config_json, experiment_config)
        for _, eval_inputs in dataset.items():
            for eval_input in eval_inputs:
                eval_input_request = eval_request_pb2.EvalInputRequest(
                    id=eval_input.id,
                    query_type=eval_input.query_type,
                    database=eval_input.database,
                    nl_prompt=eval_input.nl_prompt,
                    dialects=eval_input.dialects,
                    golden_sql=[q for q in eval_input.golden_sql if q is not None],
                    eval_query=[q for q in eval_input.eval_query if q is not None],
                    setup_sql=[q for q in eval_input.setup_sql if q is not None],
                    cleanup_sql=[q for q in eval_input.cleanup_sql if q is not None],
                    tags=eval_input.tags,
                )
                eval_input_request.other.update(eval_input.other)
                yield eval_input_request

    async def Eval(
        self,
        request_iterator: AsyncIterator[eval_request_pb2.EvalInputRequest],
        context: grpc.ServicerContext,
    ) -> eval_response_pb2.EvalResponse:
        session = SESSIONMANAGER.get_session(rpc_id_var.get())
        config, db_config, model_config, setup_config = load_session_configs(session)
        dataset = await get_dataset_from_request(request_iterator)
        core_db, model_generator, prompt_generator = create_eval_instances(
            config, db_config, model_config
        )
        evaluator = Evaluator(
            config, prompt_generator, model_generator, db_config, core_db, setup_config
        )
        evaluator.evaluate(dataset)

        job_id, run_time, results_tf, scores_tf = evaluator.process()
        reporters = get_reporters(config.get("reporting"), job_id, run_time)
        _process_results(
            reporters, job_id, run_time, results_tf, scores_tf, config, model_config, db_config
        )
        core_db.clean_tmp_creations()
        core_db.close_connections()

        return eval_response_pb2.EvalResponse(response=f"{job_id}")


def _process_results(
    reporters, job_id, run_time, results_tf, scores_tf, config, model_config, db_config
):
    config_df = config_to_df(
        job_id,
        run_time,
        config,
        model_config,
        db_config,
    )
    results = load_json(results_tf)
    results_df = report.get_dataframe(results)
    if results_df.empty:
        logging.warning(
            "There were no matching evals in this run. Returning empty set."
        )
        return eval_response_pb2.EvalResponse(response=f"{job_id}")
    report.quick_summary(results_df)
    scores = load_json(scores_tf)
    scores_df, summary_scores_df = analyzer.analyze_result(scores, config)
    summary_scores_df["job_id"] = job_id
    summary_scores_df["run_time"] = run_time

    # Store the reports in specified outputs
    for reporter in reporters:
        reporter.store(config_df, report.STORETYPE.CONFIGS)
        reporter.store(results_df, report.STORETYPE.EVALS)
        reporter.store(scores_df, report.STORETYPE.SCORES)
        reporter.store(summary_scores_df, report.STORETYPE.SUMMARY)

    # k8s emptyDir /tmp does not auto cleanup, so we explicitly delete
    pathlib.Path(results_tf).unlink()
    pathlib.Path(scores_tf).unlink()
