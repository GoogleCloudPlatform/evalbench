"""A gRPC servicer that handles EvalService requests."""

import ast
import asyncio
import json
import os
import pandas as pd
from collections.abc import AsyncIterator
from typing import AsyncGenerator

from absl import logging
from typing import Awaitable, Callable, Optional
import contextvars
import yaml
import grpc
import pathlib
import queue
from dataset.dataset import load_json
from dataset import evalinput
from evaluator import get_orchestrator, get_streaming_orchestrator

import reporting.report as report
from reporting import get_reporters
import reporting.analyzer as analyzer
from util.config import update_google3_relative_paths, set_session_configs, config_to_df
from util import get_SessionManager
from util.scriptrunner import run_script
import sys
from util.sessionmgr import SESSION_RESOURCES_PATH
from dataset.dataset import load_dataset_from_json

# protoc generates flat imports in eval_service_pb2_grpc (e.g. import
# eval_agent_pb2). Ensure evalproto is in sys.path so stubs resolve cleanly.
_PROTO_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "evalproto")
if _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

from evalproto import (
    eval_request_pb2,
    eval_response_pb2,
    eval_service_pb2_grpc,
    eval_agent_pb2,
)
from util.service import (
    load_session_configs,
    get_dataset_from_request,
)
from generators.models.grpc_proxy import PROXY_QUEUES
from generators.models.agent_grpc_proxy import AGENT_GRPC_PROXY_QUEUES

import threading
from util.context import rpc_id_var
from util import get_SessionManager


SESSIONMANAGER = get_SessionManager()


class EmptyEvalResultError(Exception):
    """Raised when an Eval run produces no result rows — a client/config issue,
    not a server fault. Translated to gRPC FAILED_PRECONDITION."""


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
            session["bidirectional_stream"] = request.bidirectional_stream
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
        # dataset_config is optional: some orchestrators drive their work from
        # the run config itself, in which case load_dataset_from_json returns {}.
        dataset_config_json = experiment_config.get("dataset_config")
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
            config, db_configs, model_config, setup_config = load_session_configs(
                session)
            if config is None:
                context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
                context.set_details("Session not configured")
                return eval_response_pb2.EvalResponse()

            config["session_id"] = session_id
            session_dir = os.path.join(SESSION_RESOURCES_PATH, session_id)

            set_up_script = config.get("set_up_script")
            if set_up_script:
                if os.path.exists(set_up_script):
                    logging.info(
                        f"Eval: Executing set_up_script '{set_up_script}'")
                    run_script(set_up_script, session_dir, "setup")
                else:
                    logging.error(
                        f"Eval: Cannot run set_up_script, file not found at '{set_up_script}'")

            streaming_eval = session.get(
                "streaming_eval", False) if session else False
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
                logging.info(
                    "Batch eval mode: evaluating all items together...")
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
                    logging.info(
                        f"Eval: Executing tear_down_script '{tear_down_script}'")
                    run_script(tear_down_script, session_dir, "teardown")
                else:
                    logging.error(
                        f"Eval: Cannot run tear_down_script, file not found at '{tear_down_script}'")

            return eval_response_pb2.EvalResponse(response=response, session_id=session_id)

        except EmptyEvalResultError as e:
            logging.warning(f"Eval produced no results: {e}")
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details(str(e))
            return eval_response_pb2.EvalResponse(session_id=session_id)

        except Exception as e:
            display_config = "Unknown"
            # Attempt retrieval of configuration details if successfully loaded
            try:
                loaded_config = SESSIONMANAGER.get_session(
                    rpc_id_var.get()).get("config", {})
                cand = loaded_config.get("dataset_config", "Unknown")
                g3_idx = cand.find("google3/")
                display_config = cand[g3_idx:] if g3_idx != -1 else cand
            except Exception as e_ctx:
                # Best effort retrieval of metadata for tracing. Do not mask original fault.
                logging.debug(
                    f"Unable to determine active dataset path for log context: {e_ctx}")

            logging.exception(
                f"gRPC Eval failed for config/dataset '{display_config}': {e}")
            raise

    async def Interact(
        self,
        request_iterator: AsyncIterator[eval_request_pb2.EvalInputRequest],
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[eval_request_pb2.EvalInputRequest, None]:
        """Bidirectional stream linking Google3 Agents to Evalbench Orchestrators."""

        session_id = rpc_id_var.get()
        session = SESSIONMANAGER.get_session(session_id)
        config, db_configs, model_config, setup_config = load_session_configs(
            session)

        if config is None:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details("Session not configured")
            return

        is_bidirectional = session.get(
            "bidirectional_stream", False) if session else False

        if not is_bidirectional:
            error_msg = (
                "Interact must be used with bidirectional streaming"
            )
            logging.error(error_msg)
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(error_msg)
            return
        logging.info("Starting a bidirectional Interact stream...")

        config["session_id"] = session_id

        # Create thread-safe queues
        in_queue = {}  # Google3 -> Evalbench (mapped by conversation_id)
        out_queue = queue.Queue()  # Evalbench -> Google3

        config["grpc_in_queues"] = in_queue
        config["grpc_out_queue"] = out_queue
        logging.info(f"CONFIG: {config}")
        generator = model_config.get("generator")

        if generator != "grpc_proxy":
            error_msg = (
                "Interactive evaluation failed: must use 'grpc_proxy' generator"
            )
            logging.error(error_msg)
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(error_msg)
            return

        # Load dataset and instantiate the Orchestrator. dataset_config is
        # optional (datasetless orchestrators drive from the run config).
        dataset_config_json = config.get("dataset_config")
        dataset_dict = load_dataset_from_json(dataset_config_json, config)

        dataset = []
        for _, item_list in dataset_dict.items():
            dataset.extend(item_list)

        num_evals = config.get("num_evals_to_run")
        if num_evals and int(num_evals) > 0:
            dataset = dataset[:int(num_evals)]

        orchestrator = get_orchestrator(
            config, db_configs, setup_config, report_progress=True)
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()

        try:
            PROXY_QUEUES[session_id] = (in_queue, out_queue)

            def _cleanup_on_drop(ctx):
                if session_id in PROXY_QUEUES:
                    PROXY_QUEUES.pop(session_id, None)
                    logging.info(
                        f"Cleaned up proxy queues for session {session_id} via disconnect callback")

            context.add_done_callback(_cleanup_on_drop)

            eval_task = loop.run_in_executor(
                None, ctx.run, orchestrator.evaluate, dataset
            )

            async def read_from_client():
                """Reads messages from the Google3 client stream."""
                async for response in request_iterator:
                    conv_id = str(
                        getattr(response, "conversation_id", getattr(response, "id", "")))
                    logging.debug(
                        "Server-Inbound: Received from Google3 for conv_id %s", conv_id)

                    if conv_id in in_queue:
                        logging.info(
                            f"[TRACE] Server-Inbound: Matched {conv_id} to active thread. Unblocking...")
                        in_queue[conv_id].put(response)
                    else:
                        logging.error(
                            "Server-Inbound: Orphaned reply! conv_id: '%s' not in active queues. Active keys: %s",
                            conv_id, list(in_queue.keys())
                        )
            read_task = asyncio.create_task(read_from_client())

            # Yield Loop: Read from out_queue and yield to Google3
            while True:
                if eval_task.done():
                    logging.info(
                        "Evaluator task finished for session %s.", session_id)
                    try:
                        eval_task.result()  # Propagate exceptions
                    except Exception as e:
                        logging.error(
                            "Orchestrator/Evaluator task failed: %s", e, exc_info=True)
                    break

                if SESSIONMANAGER.get_session(session_id) is None:
                    logging.warning(
                        f"Session {session_id} deleted. Terminating stream.")
                    context.set_code(grpc.StatusCode.NOT_FOUND)
                    context.set_details("Session deleted")
                    return

                try:
                    out_request: eval_request_pb2.EvalInputRequest = await asyncio.to_thread(out_queue.get, True, 1.0)

                    logging.debug(
                        "Server-Outbound: Yielding to Google3 for conv_id %s", out_request.conversation_id)
                    yield out_request

                except queue.Empty:
                    continue  # Loop back and check if eval_task is done
                except Exception as e:
                    import traceback
                    logging.error(
                        "Server-Outbound: Yield Loop error: %s", e, exc_info=True)
                    continue

            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                logging.debug("Read task cancelled as expected.")

            # Process final scoring and reporting
            job_id, run_time, results_tf, scores_tf = orchestrator.process()
            reporters = get_reporters(config.get(
                "reporting") or {}, job_id, run_time)

            logging.info(
                "Offloading interactive results processing to thread pool...")

            summary = await loop.run_in_executor(
                None,
                ctx.run,
                _process_results,
                reporters,
                job_id,
                run_time,
                results_tf,
                scores_tf,
                None,  # Added None for multi_trial_scores_tf
                config,
                model_config,
                db_configs,
            )
            logging.info(
                f"Finished Interactive Job ID {job_id}. Summary: {summary}")

            # Send the final payload back to the client to close the stream cleanly.
            final_request = eval_request_pb2.EvalInputRequest()
            final_request.payload = json.dumps(
                {"job_id": job_id, "summary": summary})

            if dataset:
                first_item = dataset[0]
                conv_id = str(getattr(first_item, "id", ""))
                if conv_id:
                    final_request.conversation_id = conv_id
            logging.info(f"Yielding final summary payload: {final_request}")
            yield final_request

        finally:
            # Clean up the global registry to prevent memory leaks.
            PROXY_QUEUES.pop(session_id, None)
            logging.info(f"Cleaned up proxy queues for session {session_id}")

    async def AgentInteract(
        self,
        request_iterator: AsyncIterator[eval_agent_pb2.AgentStreamMessage],
        context: grpc.ServicerContext,
    ) -> AsyncGenerator[eval_agent_pb2.AgentStreamMessage, None]:
        """Bidirectional stream linking remote autonomous agents to Evalbench
        AgentEvaluator.
        """
        session_id = rpc_id_var.get()
        session = SESSIONMANAGER.get_session(session_id)
        config, db_configs, model_config, setup_config = load_session_configs(
            session
        )

        if config is None:
            context.set_code(grpc.StatusCode.FAILED_PRECONDITION)
            context.set_details("Session not configured")
            return

        is_bidirectional = (
            session.get("bidirectional_stream", False) if session else False
        )
        if not is_bidirectional:
            error_msg = (
                "AgentInteract must be used with bidirectional streaming"
            )
            logging.error(error_msg)
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(error_msg)
            return

        generator = (model_config or {}).get("generator")
        if generator != "agent_grpc_proxy":
            error_msg = (
                "AgentInteract stream failed: run config must use "
                f"'agent_grpc_proxy' generator (received '{generator}')"
            )
            logging.error(error_msg)
            context.set_code(grpc.StatusCode.INVALID_ARGUMENT)
            context.set_details(error_msg)
            return

        logging.info(
            "Starting an AgentInteract bidirectional stream for session %s...",
            session_id,
        )
        config["session_id"] = session_id

        inboxes: dict[str, queue.Queue] = {}  # correlation_id -> queue.Queue
        out_queue = queue.Queue()  # Evalbench -> worker

        config["agent_inboxes"] = inboxes
        config["agent_out_queue"] = out_queue
        AGENT_GRPC_PROXY_QUEUES[session_id] = (inboxes, out_queue)

        # Load dataset and instantiate orchestrator
        dataset_config_json = config.get("dataset_config")
        dataset_dict = load_dataset_from_json(dataset_config_json, config)

        dataset = []
        for _, item_list in dataset_dict.items():
            dataset.extend(item_list)

        num_evals = config.get("num_evals_to_run")
        if num_evals and int(num_evals) > 0:
            dataset = dataset[:int(num_evals)]

        orchestrator = get_orchestrator(
            config, db_configs, setup_config, report_progress=True
        )
        loop = asyncio.get_event_loop()
        ctx = contextvars.copy_context()

        try:
            def _cleanup_on_drop(ctx):
                if session_id in AGENT_GRPC_PROXY_QUEUES:
                    AGENT_GRPC_PROXY_QUEUES.pop(session_id, None)
                    logging.info(
                        "Cleaned up agent proxy queues for session %s",
                        session_id,
                    )

            context.add_done_callback(_cleanup_on_drop)

            async def run_eval_and_process():
                await loop.run_in_executor(
                    None, ctx.run, orchestrator.evaluate, dataset
                )
                (
                    job_id,
                    run_time,
                    results_tf,
                    scores_tf,
                    multi_trial_scores_tf,
                ) = orchestrator.process()
                reporters = get_reporters(
                    config.get("reporting") or {}, job_id, run_time
                )
                logging.info("Processing agent evaluation results...")
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
                return job_id, summary

            eval_task = asyncio.create_task(run_eval_and_process())

            async def read_from_client():
                async for response in request_iterator:
                    corr_id = response.correlation_id
                    logging.info(
                        "Server-Inbound: Received AgentStreamMessage "
                        "(correlation_id=%s, payload=%s)",
                        corr_id,
                        response.WhichOneof("payload"),
                    )
                    if corr_id in inboxes:
                        inboxes[corr_id].put(response)
                    else:
                        logging.warning(
                            "Server-Inbound: Orphaned AgentStreamMessage "
                            "correlation_id '%s' (active inboxes: %s)",
                            corr_id,
                            list(inboxes.keys()),
                        )

            read_task = asyncio.create_task(read_from_client())

            # Yield loop: pop from out_queue and yield to worker
            job_id = None
            summary = None
            while True:
                if eval_task.done():
                    logging.info(
                        "Agent Evaluator & Reporting task finished for "
                        "session %s.",
                        session_id,
                    )
                    try:
                        job_id, summary = eval_task.result()
                    except Exception as e:
                        logging.error(
                            "Agent Evaluator & Reporting task failed: %s",
                            e,
                            exc_info=True,
                        )
                        context.set_code(grpc.StatusCode.INTERNAL)
                        context.set_details(f"Agent evaluation failed: {e}")
                        return
                    break

                if SESSIONMANAGER.get_session(session_id) is None:
                    logging.warning(
                        "Session %s deleted. Terminating stream.",
                        session_id,
                    )
                    context.set_code(grpc.StatusCode.NOT_FOUND)
                    context.set_details("Session deleted")
                    return

                try:
                    out_msg: eval_agent_pb2.AgentStreamMessage = (
                        await asyncio.to_thread(out_queue.get, True, 0.5)
                    )
                    logging.info(
                        "Server-Outbound: Yielding AgentStreamMessage "
                        "(correlation_id=%s, payload=%s)",
                        out_msg.correlation_id,
                        out_msg.WhichOneof("payload"),
                    )
                    yield out_msg
                except queue.Empty:
                    continue
                except Exception as e:
                    logging.error(
                        "Server-Outbound: Error yielding message: %s",
                        e,
                        exc_info=True,
                    )
                    continue

            # Flush any remaining messages from out_queue before finishing
            while not out_queue.empty():
                try:
                    out_msg = out_queue.get_nowait()
                    yield out_msg
                except Exception:
                    break

            read_task.cancel()
            try:
                await read_task
            except asyncio.CancelledError:
                # Expected when cleaning up inbound reader task upon eval
                pass

            if job_id and summary:
                logging.info(
                    "Finished Agent Evaluation Job ID %s. Summary: %s",
                    job_id,
                    summary,
                )
                final_msg = eval_agent_pb2.AgentStreamMessage(
                    session_id=session_id,
                    correlation_id="final_summary",
                    session_summary=eval_agent_pb2.SessionSummaryMessage(
                        job_id=job_id,
                        summary_json=json.dumps(summary),
                    ),
                )
                yield final_msg

        finally:
            AGENT_GRPC_PROXY_QUEUES.pop(session_id, None)
            logging.info(
                "Cleaned up agent proxy queues for session %s",
                session_id,
            )


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
    if results_df.empty:
        raise EmptyEvalResultError(
            "No matching evals were produced for this run. Check that the dataset, "
            "dialect filters, and database configuration line up."
        )
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

    # Add percentiles for metrics reported via the `other` map. `other` is
    # stored as a string-serialized dict in the dataframe, so parse it first.
    if "other" in results_df.columns:
        def _parse_other(o):
            if isinstance(o, dict):
                return o
            if isinstance(o, str) and o:
                try:
                    return ast.literal_eval(o)
                except (ValueError, SyntaxError):
                    return {}
            return {}

        parsed_other = results_df["other"].apply(_parse_other)
        for metric_key in (
            "time_to_first_response",
            "input_token_count",
            "output_token_count",
        ):
            values = parsed_other.apply(lambda d, k=metric_key: d.get(k))
            values = pd.to_numeric(values, errors="coerce").dropna()
            if not values.empty:
                summary[metric_key] = {
                    "p50": round(values.quantile(0.5), 2),
                    "p90": round(values.quantile(0.9), 2),
                }

    return summary
