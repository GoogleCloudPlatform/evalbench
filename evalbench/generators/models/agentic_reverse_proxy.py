"""
This module defines the AgenticReverseProxyGenerator, which acts as a proxy
within Evalbench to delegate multi-turn agent execution across a bidirectional
gRPC stream.

The AgenticReverseProxyGenerator does not launch or execute agent CLI binaries
locally. Instead, it marshals turn requests from the AgentEvaluator into
TurnRequest protobuf messages, dispatches them over the reverse stream to a
remote agent runner/sandbox, and adapts incoming TurnResponse protobuf messages
into CompletedProcess results expected by Evalbench evaluators and scorers.
"""

import json
import logging
import queue
import subprocess
import uuid
from typing import Any

from evalproto import eval_agent_pb2
from util.context import rpc_id_var

from .agent_cli import AgentCliGenerator

logger = logging.getLogger(__name__)

# Global dictionary for active reverse proxy sessions:
# session_id -> (inboxes_dict, out_queue)
AGENT_PROXY_QUEUES: dict[str, tuple[dict[str, queue.Queue], queue.Queue]] = {}


class CLICommand:
    def __init__(
        self,
        cli: str,
        prompt: str,
        env: dict | None = None,
        resume: bool = False,
        session_id: str | None = None,
        cwd: str | None = None,
    ):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.session_id = session_id
        self.cwd = cwd


class AgenticReverseProxyGenerator(AgentCliGenerator):
    """Generator proxying multi-turn agent execution across reverse stream."""

    def __init__(self, querygenerator_config: dict[str, Any]):
        super().__init__(querygenerator_config)
        self.name = "agentic_reverse_proxy"
        self.env = querygenerator_config.get("env") or {}
        self.timeout_seconds = float(
            querygenerator_config.get("timeout_seconds", 300.0)
        )
        self.turn_counter: dict[str, int] = {}
        logger.info(
            "Initialized AgenticReverseProxyGenerator (timeout=%ss)",
            self.timeout_seconds,
        )

    @property
    def version(self) -> str:
        return "agentic_reverse_proxy"

    def generate_internal(self, prompt: str) -> str:
        res = self.safe_generate(self.create_command(self.name, prompt))
        return res.stdout

    def create_command(
        self,
        cli: str,
        prompt: str,
        env: dict | None = None,
        resume: bool = False,
        session_id: str | None = None,
        cwd: str | None = None,
    ) -> CLICommand:
        merged_env = self.env.copy()
        if env:
            merged_env.update(env)
        return CLICommand(
            cli=cli or self.name,
            prompt=prompt,
            env=merged_env,
            resume=resume,
            session_id=session_id,
            cwd=cwd,
        )

    def _build_turn_request(
        self,
        cli_cmd: CLICommand,
        turn_index: int,
        timeout_seconds: float,
    ) -> eval_agent_pb2.TurnRequest:
        """Constructs a TurnRequest proto message from a CLICommand."""
        return eval_agent_pb2.TurnRequest(
            turn_index=turn_index,
            prompt=cli_cmd.prompt,
            env={
                k: str(v) for k, v in cli_cmd.env.items()
            } if cli_cmd.env else {},
            working_dir=cli_cmd.cwd or "/workspace",
            timeout_seconds=timeout_seconds,
            resume=cli_cmd.resume,
        )

    def _turn_response_to_process(
        self,
        turn_resp: eval_agent_pb2.TurnResponse,
        session_id: str,
    ) -> subprocess.CompletedProcess:
        """Converts a TurnResponse proto message into a CompletedProcess."""
        tool_calls_list = []
        tools_by_name = {}
        for tc in turn_resp.tool_calls:
            tname = tc.tool_name
            t_entry = {
                "tool_name": tname,
                "parameters": tc.parameters_json,
                "output": tc.output,
                "status": tc.status,
                "duration_ms": tc.duration_ms,
            }
            tool_calls_list.append(t_entry)
            if tname not in tools_by_name:
                tools_by_name[tname] = {"parameters": []}
            tools_by_name[tname]["parameters"].append(tc.parameters_json)

        exit_code = 0 if turn_resp.success else 1
        envelope = {
            "session_id": session_id,
            "response": turn_resp.response_text,
            "stdout": turn_resp.response_text,
            "stderr": turn_resp.error_message,
            "exit_code": exit_code,
            "execution_completed": turn_resp.execution_completed,
            "tool_calls": tool_calls_list,
            "stats": {
                "tools": {
                    "totalCalls": len(tool_calls_list),
                    "byName": tools_by_name,
                },
                "tokens": dict(turn_resp.token_stats),
            },
        }

        raw_stdout = json.dumps(envelope, indent=2)
        return subprocess.CompletedProcess(
            args=["agentic_reverse_proxy"],
            returncode=exit_code,
            stdout=raw_stdout,
            stderr=turn_resp.error_message,
        )

    def safe_generate(
        self,
        cli_cmd: CLICommand,
        timeout_seconds: float | None = None,
    ) -> subprocess.CompletedProcess:
        session_id = cli_cmd.session_id or rpc_id_var.get()
        effective_timeout = (
            timeout_seconds
            if timeout_seconds is not None
            else self.timeout_seconds
        )

        if session_id not in AGENT_PROXY_QUEUES:
            ctx_id = rpc_id_var.get()
            if ctx_id in AGENT_PROXY_QUEUES:
                session_id = ctx_id
            else:
                logger.error(
                    "AgenticReverseProxy: session_id %s not in "
                    "AGENT_PROXY_QUEUES (keys: %s)",
                    session_id,
                    list(AGENT_PROXY_QUEUES.keys()),
                )
                return subprocess.CompletedProcess(
                    args=["agentic_reverse_proxy"],
                    returncode=1,
                    stdout="",
                    stderr=f"Session {session_id} not connected to stream",
                )

        inboxes, out_queue = AGENT_PROXY_QUEUES[session_id]

        turn_idx = self.turn_counter.get(session_id, 0) + 1
        self.turn_counter[session_id] = turn_idx

        correlation_id = str(uuid.uuid4())
        inbox: queue.Queue[eval_agent_pb2.AgentStreamMessage] = queue.Queue()
        inboxes[correlation_id] = inbox

        turn_req = self._build_turn_request(
            cli_cmd=cli_cmd,
            turn_index=turn_idx,
            timeout_seconds=effective_timeout,
        )
        msg = eval_agent_pb2.AgentStreamMessage(
            session_id=session_id,
            correlation_id=correlation_id,
            turn_request=turn_req,
        )

        logger.info(
            "[REVERSE_PROXY] Dispatching TurnRequest turn=%d "
            "(correlation_id=%s) to out_queue",
            turn_idx,
            correlation_id,
        )
        out_queue.put(msg)

        try:
            resp_msg = inbox.get(timeout=effective_timeout)
        except queue.Empty:
            logger.error(
                "[REVERSE_PROXY] Timed out waiting for TurnResponse "
                "(correlation_id=%s)",
                correlation_id,
            )
            return subprocess.CompletedProcess(
                args=["agentic_reverse_proxy"],
                returncode=124,
                stdout="",
                stderr="Timed out waiting for agent response from stream",
            )
        finally:
            inboxes.pop(correlation_id, None)

        if not resp_msg.HasField("turn_response"):
            err_details = resp_msg.WhichOneof("payload")
            logger.error(
                "[REVERSE_PROXY] Unexpected message received on inbox: %s",
                err_details,
            )
            return subprocess.CompletedProcess(
                args=["agentic_reverse_proxy"],
                returncode=1,
                stdout="",
                stderr=f"Unexpected payload on stream: {err_details}",
            )

        return self._turn_response_to_process(
            turn_resp=resp_msg.turn_response,
            session_id=session_id,
        )

    def parse_response(self, stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except Exception:
            return {"response": stdout}

    def extract_tools(self, stdout: str) -> list[str]:
        output_json = self.parse_response(stdout)
        if "stats" in output_json and "tools" in output_json["stats"] and "byName" in output_json["stats"]["tools"]:
            return list(output_json["stats"]["tools"]["byName"].keys())
        if "tool_calls" in output_json:
            return [tc.get("tool_name") for tc in output_json["tool_calls"] if isinstance(tc, dict) and "tool_name" in tc]
        return []

    def extract_skills(self, stdout: str) -> list[str]:
        output_json = self.parse_response(stdout)
        skills = []
        for tc in output_json.get("tool_calls", []):
            if isinstance(tc, dict) and tc.get("tool_name") in ("activate_skill", "Skill", "use_skill"):
                params = tc.get("parameters", {})
                if isinstance(params, str):
                    try:
                        params = json.loads(params)
                    except Exception:
                        params = {}
                if isinstance(params, dict):
                    sname = (
                        params.get("skill_name")
                        or params.get("skillName")
                        or params.get("skill")
                        or params.get("name")
                    )
                    if sname and sname not in skills:
                        skills.append(sname)
        return skills
