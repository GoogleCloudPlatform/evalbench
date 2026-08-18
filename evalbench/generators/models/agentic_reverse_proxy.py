import json
import logging
import queue
import subprocess
import uuid
from typing import Any, Dict

from .agent_cli import AgentCliGenerator
from evalproto import eval_agent_pb2
from util.context import rpc_id_var

# Global dictionary for active reverse proxy sessions:
# session_id -> (inboxes_dict, out_queue)
AGENT_PROXY_QUEUES: Dict[str, tuple[Dict[str, queue.Queue], queue.Queue]] = {}


class CLICommand:
    def __init__(self, cli: str, prompt: str, env: dict = None, resume: bool = False, session_id: str = None, cwd: str = None):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.session_id = session_id
        self.cwd = cwd


class AgenticReverseProxyGenerator(AgentCliGenerator):
    """Generator proxying multi-turn agent execution across the reverse bidi stream."""

    def __init__(self, querygenerator_config: Dict[str, Any]):
        super().__init__(querygenerator_config)
        self.name = "agentic_reverse_proxy"
        self.timeout_seconds = float(querygenerator_config.get("timeout_seconds", 300.0))
        self.turn_counter: Dict[str, int] = {}
        logging.info("Initialized AgenticReverseProxyGenerator (timeout=%ss)", self.timeout_seconds)

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
        env: dict = None,
        resume: bool = False,
        session_id: str = None,
        cwd: str = None,
    ) -> CLICommand:
        return CLICommand(
            cli=cli or self.name,
            prompt=prompt,
            env=env,
            resume=resume,
            session_id=session_id,
            cwd=cwd,
        )

    def safe_generate(self, cli_cmd: CLICommand | dict | str) -> subprocess.CompletedProcess:
        if isinstance(cli_cmd, CLICommand):
            prompt = cli_cmd.prompt
            session_id = cli_cmd.session_id or rpc_id_var.get()
            resume = cli_cmd.resume
            env = cli_cmd.env
            cwd = cli_cmd.cwd
        elif isinstance(cli_cmd, dict):
            prompt = cli_cmd.get("prompt", "")
            session_id = cli_cmd.get("session_id") or rpc_id_var.get()
            resume = cli_cmd.get("resume", False)
            env = cli_cmd.get("env", {})
            cwd = cli_cmd.get("cwd")
        else:
            prompt = str(cli_cmd)
            session_id = rpc_id_var.get()
            resume = False
            env = {}
            cwd = None

        if session_id not in AGENT_PROXY_QUEUES:
            ctx_id = rpc_id_var.get()
            if ctx_id in AGENT_PROXY_QUEUES:
                session_id = ctx_id
            else:
                logging.error("AgenticReverseProxy: session_id %s not in AGENT_PROXY_QUEUES (keys: %s)", session_id, list(AGENT_PROXY_QUEUES.keys()))
                return subprocess.CompletedProcess(
                    args=["agentic_reverse_proxy"],
                    returncode=1,
                    stdout="",
                    stderr=f"Session {session_id} not connected to reverse stream",
                )

        inboxes, out_queue = AGENT_PROXY_QUEUES[session_id]

        turn_idx = self.turn_counter.get(session_id, 0) + 1
        self.turn_counter[session_id] = turn_idx

        correlation_id = str(uuid.uuid4())
        inbox: queue.Queue[eval_agent_pb2.AgentStreamMessage] = queue.Queue()
        inboxes[correlation_id] = inbox

        turn_req = eval_agent_pb2.TurnRequest(
            turn_index=turn_idx,
            prompt=prompt,
            env={k: str(v) for k, v in env.items()} if env else {},
            working_dir=cwd or "/workspace",
            timeout_seconds=self.timeout_seconds,
            resume=resume,
        )

        msg = eval_agent_pb2.AgentStreamMessage(
            session_id=session_id,
            correlation_id=correlation_id,
            turn_request=turn_req,
        )

        logging.info("[REVERSE_PROXY] Dispatching TurnRequest turn=%d (correlation_id=%s) to out_queue", turn_idx, correlation_id)
        out_queue.put(msg)

        try:
            resp_msg = inbox.get(timeout=self.timeout_seconds)
        except queue.Empty:
            logging.error("[REVERSE_PROXY] Timed out waiting for TurnResponse (correlation_id=%s)", correlation_id)
            return subprocess.CompletedProcess(
                args=["agentic_reverse_proxy"],
                returncode=124,
                stdout="",
                stderr="Timed out waiting for agent response from reverse stream",
            )
        finally:
            inboxes.pop(correlation_id, None)

        if not resp_msg.HasField("turn_response"):
            err_details = resp_msg.WhichOneof("payload")
            logging.error("[REVERSE_PROXY] Unexpected message received on inbox: %s", err_details)
            return subprocess.CompletedProcess(
                args=["agentic_reverse_proxy"],
                returncode=1,
                stdout="",
                stderr=f"Unexpected payload on stream: {err_details}",
            )

        turn_resp = resp_msg.turn_response
        tool_calls_list = []
        tools_by_name = {}
        for tc in turn_resp.tool_calls:
            t_entry = {
                "tool_name": tc.tool_name,
                "parameters": tc.parameters_json,
                "output": tc.output,
                "status": tc.status,
                "duration_ms": tc.duration_ms,
            }
            tool_calls_list.append(t_entry)
            if tc.tool_name not in tools_by_name:
                tools_by_name[tc.tool_name] = {"parameters": []}
            tools_by_name[tc.tool_name]["parameters"].append(tc.parameters_json)

        envelope = {
            "session_id": session_id,
            "response": turn_resp.response_text,
            "stdout": turn_resp.stdout,
            "stderr": turn_resp.stderr,
            "exit_code": turn_resp.exit_code,
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
            returncode=turn_resp.exit_code,
            stdout=raw_stdout,
            stderr=turn_resp.stderr,
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
                    sname = params.get("skill_name") or params.get("skill") or params.get("name")
                    if sname and sname not in skills:
                        skills.append(sname)
        return skills
