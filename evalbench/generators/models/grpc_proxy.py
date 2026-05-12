"""
This module defines the GrpcProxyModel, which acts as a client-side interface
within the Evalbench system to an external generator running in Google3.

The GrpcProxyModel does not perform any generation itself. Instead, it marshals
requests from the Evalbench evaluator (e.g., CortadoEvaluator), sends them
over a bidirectional gRPC stream to the Google3 orchestrator, and waits for
the results to be sent back.
"""

import threading
import queue
import logging
import traceback
from generators.models.generator import QueryGenerator
from evalproto import eval_request_pb2

PROXY_QUEUES = {}


class GrpcProxyModel(QueryGenerator):
    def __init__(self, config):
        super().__init__(config)
        self.name = "grpc_proxy"

    def generate_internal(self, prompt: str) -> str:
        # This method seems unused in the bidi flow, can be left as pass
        pass

    def generate(self, eval_output) -> dict:
        """
        Proxies the request to Google3 and waits for a response.
        eval_output is typically an instance of EvalCortadoRequest.
        """
        conv_id = None  # Initialize conv_id
        in_queues_dict = None  # Initialize in_queues_dict
        thread_id = threading.get_ident()
        try:
            if not PROXY_QUEUES:
                raise RuntimeError("PROXY_QUEUES is empty!")

            # Assuming single session per proxy for now
            session_id = list(PROXY_QUEUES.keys())[0]
            in_queues_dict, out_queue = PROXY_QUEUES[session_id]

            def get_val(obj, *keys, default=None):
                for k in keys:
                    if hasattr(obj, "get") and callable(obj.get):
                        val = obj.get(k)
                        if val is not None:
                            return val
                    if hasattr(obj, k):
                        val = getattr(obj, k)
                        if val is not None:
                            return val
                return default

            prompt_text = get_val(eval_output, "nl_prompt", default="")
            database = get_val(eval_output, "database",
                               "db_id", "database_name", default="")
            dialects = get_val(eval_output, "dialects", "dialect", default=[])
            if isinstance(dialects, str):
                dialects = [dialects]
            query_type = get_val(eval_output, "query_type", default="")
            scenario_payload = get_val(
                eval_output, "payload_str", "payload", default="{}")
            item_id_str = str(
                get_val(eval_output, "id", "eval_id", default=thread_id))
            conv_id = item_id_str

            thread_inbox = queue.Queue()
            if conv_id in in_queues_dict:
                logging.warning(
                    f"[TRACE] Proxy[Thread-{thread_id}]: WARNING: conv_id {conv_id} already exists in in_queue. Overwriting.")
            in_queues_dict[conv_id] = thread_inbox

            outbound_req = eval_request_pb2.EvalInputRequest(
                conversation_id=conv_id,
                nl_prompt=prompt_text,
                database=database,
                dialects=dialects,
                query_type=query_type,
                payload=scenario_payload
            )

            logging.debug(
                f"[DEBUG] Routing prompt to client. conv_id: {conv_id}")
            out_queue.put(outbound_req)

            logging.debug(
                f"[TRACE] Blocked and waiting for client reply on {conv_id}...")
            inbound_response: eval_request_pb2.EvalInputRequest = thread_inbox.get(
                block=True, timeout=300.0)
            logging.debug(
                f"[TRACE]  Received Reply from client for conv_id {conv_id}!")

            # Extract fields from the received proto
            nl_response = getattr(
                inbound_response, "generated_nl_response", "")
            sql_response = getattr(inbound_response, "generated_sql", "")

            # Update the eval_output object with the results from the client.
            if hasattr(eval_output, "__setitem__"):
                eval_output["generated_sql"] = sql_response
                eval_output["generated_nl_response"] = nl_response
            else:
                setattr(eval_output, "generated_sql", sql_response)
                setattr(eval_output, "generated_nl_response", nl_response)

            return eval_output

        except queue.Empty:
            logging.error(f"[ERROR] Client TIMEOUT on {conv_id}")
            raise TimeoutError(
                f"Client disconnected or timed out on conv_id {conv_id}.")
        except Exception as e:
            logging.error(
                f"[ERROR] crashed hard on conv_id {conv_id}: {e}\n{traceback.format_exc()}")
            raise e
        finally:
            if in_queues_dict is not None and conv_id is not None:
                in_queues_dict.pop(conv_id, None)
                logging.debug(f"[DEBUG] Cleaned up inbox for {conv_id}")
