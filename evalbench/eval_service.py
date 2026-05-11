"""A gRPC servicer that handles EvalService requests."""

import asyncio
import json
import os
from collections.abc import AsyncIterator
from typing import AsyncGenerator

from absl import logging
from typing import Awaitable, Callable, Optional
import contextvars
import yaml
import grpc
import pathlib
from dataset.dataset import load_json
from dataset import evalinput
from evaluator import get_orchestrator, get_streaming_orchestrator

import reporting.report as report
from reporting import get_reporters
import reporting.analyzer as analyzer
from util.config import update_google3_relative_paths, set_session_configs, config_to_df
from util import get_SessionManager
from util.scriptrunner import run_script
from util.sessionmgr import SESSION_RESOURCES_PATH
from dataset.dataset import load_dataset_from_json
from evalproto import (
    eval_request_pb2,
    eval_response_pb2,
    eval_service_pb2_grpc,
)
from util.service import (
    load_session_configs,
    get_dataset_from_request,
)

import threading
from util.context import rpc_id_var
from util import get_SessionManager

SESSIONMANAGER = get_SessionManager()


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
        _metadata = dict(handler_call_details.invocation_metadata)
        if rpc_id_var.get() == "default":
            _metadata = dict(handler_call_details.invocation_metadata)
            rpc_id_var.set(_metadata["client-rpc-id"])
            SESSIONMANAGER.create_session(rpc_id_var.get())
        return await continuation(handler_call_details)


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
        session_id = rpc_id_var.get()
        return eval_response_pb2.EvalResponse(response="ack", session_id=session_id)

    async def Connect(
        self,
        request,
        context,
    ) -> eval_response_pb2.EvalResponse:
        session_id = rpc_id_var.get()
        session = SESSIONMANAGER.get_session(session_id)
        if session is not None:
            session["streaming_eval"] = request.streaming_eval
        return eval_response_pb2.EvalResponse(response="ack", session_id=session_id)

    async def EvalConfig(
        self,
        request,
        context,
    ) -> eval_response_pb2.EvalResponse:
        resource_map = {r.address: r.address for r in request.resources}
        experiment_config = yaml.safe_load(request.yaml_config.decode("utf-8"))
        update_google3_relative_paths(
            experiment_config, rpc_id_var.get(), resource_map)
        for resource in request.resources:
            if resource.address.endswith(".yaml"):
                yaml_config = yaml.safe_load(resource.content.decode("utf-8"))
                update_google3_relative_paths(
                    yaml_config, rpc_id_var.get(), resource_map)
                resource.content = yaml.dump(yaml_config).encode("utf-8")
        session = SESSIONMANAGER.get_session(rpc_id_var.get())
        SESSIONMANAGER.write_resource_files(
            rpc_id_var.get(), request.resources)
        set_session_configs(session, experiment_config)
        session_id = rpc_id_var.get()
        return eval_response_pb2.EvalResponse(response="ack", session_id=session_id)

    async def ListEvalInputs(
        self,
        request,
        context,
    ) -> AsyncGenerator[eval_request_pb2.EvalInputRequest, None]:
        session = SESSIONMANAGER.get_session(rpc_id_var.get())
        logging.info("Retrieving Evals for: %s.", rpc_id_var.get())
        experiment_config = session["config"]
        dataset_config_json = experiment_config["dataset_config"]
        dataset = load_dataset_from_json(
            dataset_config_json, experiment_config)
        for _, eval_inputs in dataset.items():
            for eval_input in eval_inputs:
                eval_input_request = eval_input.to_proto()
                yield eval_input_request

    async def Eval(
        self,
        request_iterator: AsyncIterator[eval_request_pb2.EvalInputRequest],
        context: grpc.ServicerContext,
    ) -> eval_response_pb2.EvalResponse:
        try:
            session_id = rpc_id_var.get()
            session = SESSIONMANAGER.get_session(session_id)
            config, db_configs, model_config, setup_config = load_session_configs(session)
            if config is None:
                context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                context.set_details("Session not configured")
                return eval_response_pb2.EvalResponse()

            config["session_id"] = session_id
            session_dir = os.path.join(SESSION_RESOURCES_PATH, session_id)

            set_up_script = config.get("set_up_script")
            if set_up_script:
                if os.path.exists(set_up_script):
                    logging.info(f"Eval: Executing set_up_script '{set_up_script}'")
                    run_script(set_up_script, session_dir, "setup")
                else:
                    logging.error(f"Eval: Cannot run set_up_script, file not found at '{set_up_script}'")

            streaming_eval = session.get("streaming_eval", False) if session else False
            loop = asyncio.get_event_loop()

            if streaming_eval:
                evaluator = get_streaming_orchestrator(
                    config, db_configs, setup_config, report_progress=True
                )
                logging.info(
                    "Streaming eval mode: evaluating items as they arrive..."
                )
                tasks = []
                async for request in request_iterator:
                    eval_input = evalinput.EvalInputRequest.init_from_proto(
                        request
                    )
                    ctx = contextvars.copy_context()

                    task = loop.run_in_executor(
                        None, ctx.run, evaluator.evaluate_item, eval_input
                    )
                    tasks.append(task)
                await asyncio.gather(*tasks)
            else:
                dataset = await get_dataset_from_request(request_iterator)
                evaluator = get_orchestrator(
                    config, db_configs, setup_config, report_progress=True
                )
                logging.info("Batch eval mode: evaluating all items together...")
                ctx = contextvars.copy_context()
                await loop.run_in_executor(
                    None, ctx.run, evaluator.evaluate, dataset
                )

            job_id, run_time, results_tf, scores_tf, multi_trial_scores_tf = evaluator.process()
            # Fallback to empty dict if reporting is present but null in YAML
            reporters = get_reporters(
                config.get("reporting") or {}, job_id, run_time
            )

            # Offload blocking results processing to a thread pool
            logging.info("Offloading results processing to thread pool...")
            ctx = contextvars.copy_context()
            summary = await loop.run_in_executor(
                None,
                ctx.run,
                _process_results,
                reporters,
                job_id,
                run_time,
                results_tf,
                scores_tf,
                multi_trial_scores_tf,
                config,
                model_config,
                db_configs,
            )

            logging.info(
                f"Finished Job ID {job_id} Thread count:{threading.active_count()}"
            )

            if config.get("summary_in_response"):
                response = json.dumps({"job_id": job_id, "summary": summary})
            else:
                response = f"{job_id}"

            tear_down_script = config.get("tear_down_script")
            if tear_down_script:
                if os.path.exists(tear_down_script):
                    logging.info(f"Eval: Executing tear_down_script '{tear_down_script}'")
                    run_script(tear_down_script, session_dir, "teardown")
                else:
                    logging.error(f"Eval: Cannot run tear_down_script, file not found at '{tear_down_script}'")

            return eval_response_pb2.EvalResponse(response=response, session_id=session_id)

        except Exception as e:
            display_config = "Unknown"
            # Attempt retrieval of configuration details if successfully loaded
            try:
                loaded_config = SESSIONMANAGER.get_session(rpc_id_var.get()).get("config", {})
                cand = loaded_config.get("dataset_config", "Unknown")
                g3_idx = cand.find("google3/")
                display_config = cand[g3_idx:] if g3_idx != -1 else cand
            except Exception as e_ctx:
                # Best effort retrieval of metadata for tracing. Do not mask original fault.
                logging.debug(f"Unable to determine active dataset path for log context: {e_ctx}")

            logging.exception(f"gRPC Eval failed for config/dataset '{display_config}': {e}")
            raise


def _process_results(
    reporters, job_id, run_time, results_tf, scores_tf, multi_trial_scores_tf, config, model_config, db_configs
):
    config_df = config_to_df(
        job_id,
        run_time,
        config,
        model_config,
        db_configs,
    )
    results = load_json(results_tf)
    results_df = report.get_dataframe(results)
    assert not results_df.empty, "There were no matching evals in this run."
    report.quick_summary(results_df)
    scores = load_json(scores_tf)
    if multi_trial_scores_tf:
        multi_trial_scores = load_json(multi_trial_scores_tf)
        if multi_trial_scores:
            scores.extend(multi_trial_scores)

    num_prompts = len(set(r.get("prompt_id")
                      for r in results if r.get("prompt_id")))
    num_trials = config.get("num_trials", 1)
    scores_df, summary_scores_df = analyzer.analyze_result(
        scores, config, num_prompts=num_prompts, num_trials=num_trials
    )
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
    if multi_trial_scores_tf:
        pathlib.Path(multi_trial_scores_tf).unlink()

    # Build summary dict from summary_scores_df
    summary = {"total": 0, "scores": {}}
    for _, row in summary_scores_df.iterrows():
        name = row.get("metric_name", "")
        total = int(row.get("total_results_count", 0))
        correct = int(row.get("correct_results_count", 0))
        summary["total"] = total
        summary["scores"][name] = correct

    # Add generation latency percentiles
    if "sql_generator_time" in results_df.columns:
        latencies = results_df["sql_generator_time"].dropna().astype(float)
        if not latencies.empty:
            summary["generation_latency"] = {
                "p50": round(latencies.quantile(0.5), 2),
                "p90": round(latencies.quantile(0.9), 2),
            }

    return summary
