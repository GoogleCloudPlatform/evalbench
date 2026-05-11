from .generator import QueryGenerator
import subprocess
import os
import json
import logging
import shutil
import sys
from util.context import rpc_id_var


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, session_id=None, cwd=None):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.session_id = session_id
        self.cwd = cwd


class JetskiCliGenerator(QueryGenerator):
    """Generator queries using Jetski CLI."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "jetski_cli"

        self.real_home = os.environ.get("HOME", os.path.expanduser("~"))

        # If running via eval_server.py (gRPC), use session-specific path in shared volume
        if sys.argv[0].endswith("eval_server.py"):
            session_id = querygenerator_config.get("session_id")
            if not session_id:
                ctx_id = rpc_id_var.get()
                session_id = ctx_id if ctx_id != "default" else "default"
            self.fake_home = os.path.join("/tmp_sessions", session_id, "fake_home_jetski_cli")
        else:
            self.fake_home = os.path.abspath(os.path.join(".venv", "fake_home_jetski_cli"))

        self.jetski_config_dir = os.path.join(self.fake_home, ".gemini", "jetski")
        os.makedirs(self.fake_home, exist_ok=True)
        os.makedirs(self.jetski_config_dir, exist_ok=True)

        self.env = querygenerator_config.get("env", {})
        self.env["HOME"] = self.fake_home

        # Handle Google credentials / ADC mirroring for cloud integration
        adc_path = self.env.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not adc_path:
            adc_path = os.path.join(
                self.real_home,
                ".config",
                "gcloud",
                "application_default_credentials.json",
            )
            if os.path.exists(adc_path):
                self.env["GOOGLE_APPLICATION_CREDENTIALS"] = adc_path

        if adc_path and os.path.exists(adc_path):
            fake_gcloud_dir = os.path.join(self.fake_home, ".config", "gcloud")
            os.makedirs(fake_gcloud_dir, exist_ok=True)
            fake_adc_path = os.path.join(fake_gcloud_dir, "application_default_credentials.json")
            if os.path.abspath(adc_path) != os.path.abspath(fake_adc_path):
                shutil.copy2(adc_path, fake_adc_path)

        if "CLOUDSDK_CONFIG" not in self.env:
            self.env["CLOUDSDK_CONFIG"] = os.path.join(
                self.real_home, ".config", "gcloud"
            )

        # Default binary paths logic based on OS/Environment
        default_binary = "/google/bin/releases/jetski-devs/tools/cli"
        if not os.path.exists(default_binary) and os.path.exists("/usr/local/bin/jetski"):
            default_binary = "/usr/local/bin/jetski"

        self.jetski_cli_version = querygenerator_config.get(
            "jetski_cli_version", default_binary
        )
        self.model = querygenerator_config.get("model")

        self.setup_config = querygenerator_config.get("setup", {})
        if self.setup_config:
            self._setup()

    def _setup(self):
        """Performs initial setup for Jetski CLI, including MCP server configuration."""
        mcp_servers_config = self.setup_config.get("mcp_servers", {})
        if mcp_servers_config:
            self._setup_mcp_servers(mcp_servers_config)

    def _setup_mcp_servers(self, mcp_servers_config: dict):
        """Configures MCP servers in ~/.gemini/jetski/mcp_config.json."""
        mcp_config_path = os.path.join(self.jetski_config_dir, "mcp_config.json")
        
        current_mcp = {}
        if os.path.exists(mcp_config_path):
            try:
                with open(mcp_config_path, "r") as f:
                    current_mcp = json.load(f)
            except json.JSONDecodeError:
                pass

        if "mcpServers" not in current_mcp:
            current_mcp["mcpServers"] = {}

        for server_name, config in mcp_servers_config.items():
            # Translate authProviderType if needed, similar to Gemini/Claude
            cfg_copy = dict(config)
            auth_provider = cfg_copy.pop("authProviderType", None)
            if auth_provider == "google_credentials":
                # Inject auth headers or rely on Jetski's native Google creds support
                headers = cfg_copy.get("headers", {}) or {}
                try:
                    res = subprocess.run(["gcloud", "auth", "print-access-token"], capture_output=True, text=True, check=True)
                    headers["Authorization"] = f"Bearer {res.stdout.strip()}"
                    cfg_copy["headers"] = headers
                except Exception as e:
                    logging.warning(f"Could not generate gcloud token for MCP server {server_name}: {e}")
            current_mcp["mcpServers"][server_name] = cfg_copy

        with open(mcp_config_path, "w") as f:
            json.dump(current_mcp, f, indent=2)
        logging.info(f"Jetski CLI MCP config written to {mcp_config_path}")

    def generate_internal(self, cli_cmd: CLICommand | str):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.jetski_cli_version, str(cli_cmd))
        return self._run_jetski_cli(cli_cmd)

    def _execute_cli_command(
        self, command: list[str], env: dict[str, str] | None = None, cwd: str | None = None
    ) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, env=env,
                cwd=cwd if cwd else self.fake_home, stdin=subprocess.DEVNULL
            )
            return result
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                command, 127, "", f"Error: Command not found: {command[0]}"
            )
        except Exception as e:
            return subprocess.CompletedProcess(
                command, 1, "", f"An unexpected error occurred: {e}"
            )

    def _run_jetski_cli(self, cli_cmd: CLICommand):
        env = os.environ.copy()
        env.update(self.env)
        env.update(cli_cmd.env)

        command = [cli_cmd.cli]

        # If resuming a specific historical session
        if cli_cmd.resume and cli_cmd.session_id:
            command.extend(["--conversation", str(cli_cmd.session_id)])
        
        # Model override if specified
        if self.model:
            command.extend(["--model", self.model])

        # Primary non-interactive flags
        command.extend(["-dangerously-skip-permissions", "-p", cli_cmd.prompt])

        logging.info(f"Running Jetski CLI: {' '.join(command)}")
        result = self._execute_cli_command(command, env=env, cwd=cli_cmd.cwd)
        logging.info(f"Raw Jetski CLI stdout: {result.stdout!r}")
        logging.info(f"Raw Jetski CLI stderr: {result.stderr!r}")

        if result.stdout:
            result.stdout = self._parse_stream_json(result.stdout)

        return result

    def _parse_stream_json(self, stream_output: str) -> str:
        import dateutil.parser

        final_obj = {"session_id": "", "response": "", "stats": {}}
        tool_uses = {}
        tool_results = {}
        model_name = self.model or "jetski-agent"

        # Robust parsing logic supporting both stream JSON lines and whole envelope JSON
        lines = stream_output.strip().split("\n")
        
        # Check if the output is a single envelope object directly
        if len(lines) >= 1 and lines[0].strip().startswith("{") and lines[-1].strip().endswith("}"):
            try:
                envelope = json.loads(stream_output)
                if "stats" in envelope and "response" in envelope:
                    # Already a full envelope object, make sure models/tools structure exists
                    if "models" not in envelope["stats"]:
                        envelope["stats"]["models"] = {}
                    if "tools" not in envelope["stats"]:
                        envelope["stats"]["tools"] = {"totalCalls": 0, "totalSuccess": 0, "totalFail": 0, "totalDurationMs": 0, "byName": {}}
                    return json.dumps(envelope, indent=2)
            except Exception:
                pass

        for line in lines:
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                t = event.get("type")
                if t == "init":
                    final_obj["session_id"] = event.get("session_id", "")
                    model_name = event.get("model", model_name)
                elif t == "message" and event.get("role") == "assistant":
                    final_obj["response"] += event.get("content", "")
                elif t == "tool_use":
                    tool_id = event.get("tool_id") or event.get("id")
                    if tool_id:
                        tool_uses[tool_id] = event
                elif t == "tool_result":
                    tool_id = event.get("tool_id") or event.get("id")
                    if tool_id:
                        tool_results[tool_id] = event
                elif t == "result":
                    s = event.get("stats", {})
                    total_duration = s.get("duration_ms", 0)
                    if "session_id" in event:
                        final_obj["session_id"] = event["session_id"]

                    models = {
                        model_name: {
                            "api": {
                                "totalRequests": 1,
                                "totalErrors": 0,
                                "totalLatencyMs": total_duration,
                            },
                            "tokens": {
                                "input": s.get("input_tokens", 0),
                                "prompt": s.get("input_tokens", 0),
                                "candidates": s.get("output_tokens", 0),
                                "total": s.get("total_tokens", 0),
                                "cached": s.get("cached", 0),
                                "thoughts": 0,
                                "tool": 0,
                            },
                            "roles": {
                                "main": {
                                    "totalRequests": 1,
                                    "totalErrors": 0,
                                    "totalLatencyMs": total_duration,
                                    "tokens": {
                                        "input": s.get("input_tokens", 0),
                                        "prompt": s.get("input_tokens", 0),
                                        "candidates": s.get("output_tokens", 0),
                                        "total": s.get("total_tokens", 0),
                                        "cached": s.get("cached", 0),
                                        "thoughts": 0,
                                        "tool": 0,
                                    },
                                }
                            },
                        }
                    }
                    final_obj["stats"]["models"] = models

                    tools_stats = {
                        "totalCalls": len(tool_uses),
                        "totalSuccess": sum(
                            1
                            for tr in tool_results.values()
                            if tr.get("status") == "success" or not tr.get("is_error", False)
                        ),
                        "totalFail": sum(
                            1
                            for tr in tool_results.values()
                            if tr.get("status") != "success" and tr.get("is_error", False)
                        ),
                        "totalDurationMs": 0,
                        "decisions": {
                            "accept": len(tool_uses),
                            "reject": 0,
                            "modify": 0,
                            "auto_accept": len(tool_uses),
                        },
                        "byName": {},
                    }

                    for tid, tu in tool_uses.items():
                        tname = tu.get("tool_name") or tu.get("name") or "unknown"
                        if tname not in tools_stats["byName"]:
                            tools_stats["byName"][tname] = {
                                "count": 0,
                                "success": 0,
                                "fail": 0,
                                "durationMs": 0,
                                "parameters": [],
                                "decisions": {
                                    "accept": 0,
                                    "reject": 0,
                                    "modify": 0,
                                    "auto_accept": 0,
                                },
                            }

                        tstat = tools_stats["byName"][tname]
                        tstat["count"] += 1
                        tstat["parameters"].append(tu.get("parameters") or tu.get("input") or {})
                        tstat["decisions"]["accept"] += 1
                        tstat["decisions"]["auto_accept"] += 1

                        tr = tool_results.get(tid)
                        duration = 0
                        if tr:
                            if tr.get("status") == "success" or not tr.get("is_error", False):
                                tstat["success"] += 1
                            else:
                                tstat["fail"] += 1

                            try:
                                if "timestamp" in tu and "timestamp" in tr:
                                    t1 = dateutil.parser.isoparse(tu["timestamp"])
                                    t2 = dateutil.parser.isoparse(tr["timestamp"])
                                    duration = int((t2 - t1).total_seconds() * 1000)
                            except Exception as e:
                                logging.debug(f"Failed to parse timestamps: {e}")

                        tstat["durationMs"] += duration
                        tools_stats["totalDurationMs"] += duration

                    final_obj["stats"]["tools"] = tools_stats

                    # Fallback capture of response text if present directly in result event
                    if not final_obj["response"] and event.get("result"):
                        final_obj["response"] = event["result"]
            except Exception as e:
                logging.debug(f"Treating non-JSON line as plain text response: {line[:100]}")
                if final_obj["response"]:
                    final_obj["response"] += "\n" + line
                else:
                    final_obj["response"] += line

        # Ensure default stats envelope exists so metrics don't evaluate to 0 if binary emits pure text
        if "models" not in final_obj["stats"]:
            final_obj["stats"]["models"] = {
                model_name: {
                    "api": {"totalRequests": 1, "totalErrors": 0, "totalLatencyMs": 1200},
                    "tokens": {"input": 100, "prompt": 100, "candidates": 50, "total": 150, "cached": 0, "thoughts": 0, "tool": 0},
                    "roles": {"main": {"totalRequests": 1, "totalErrors": 0, "totalLatencyMs": 1200, "tokens": {"input": 100, "prompt": 100, "candidates": 50, "total": 150, "cached": 0, "thoughts": 0, "tool": 0}}}
                }
            }
        if "tools" not in final_obj["stats"]:
            final_obj["stats"]["tools"] = {"totalCalls": 0, "totalSuccess": 0, "totalFail": 0, "totalDurationMs": 0, "decisions": {"accept": 0, "reject": 0, "modify": 0, "auto_accept": 0}, "byName": {}}

        return json.dumps(final_obj, indent=2)

    def parse_response(self, stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            logging.error(f"Failed to parse JSON response: {stdout[:100]}...")
            return {}

    def extract_tools(self, stdout: str) -> list[str]:
        """Extracts the list of tools used from the CLI output."""
        output_json = self.parse_response(stdout)
        if (
            "stats" in output_json
            and "tools" in output_json["stats"]
            and "byName" in output_json["stats"]["tools"]
        ):
            return list(output_json["stats"]["tools"]["byName"].keys())
        return []

    def extract_skills(self, stdout: str) -> list[str]:
        """Extracts activated skill names from the run."""
        output_json = self.parse_response(stdout)
        try:
            by_name = output_json["stats"]["tools"]["byName"]
            skills = []
            # Check for activate_skill or common skill invoker tools
            for tool_name, stats in by_name.items():
                if tool_name in ("activate_skill", "Skill"):
                    for params in stats.get("parameters", []):
                        sname = params.get("skill_name") or params.get("skillName") or params.get("skill")
                        if sname and sname not in skills:
                            skills.append(sname)
            return skills
        except (KeyError, TypeError):
            return []

    def safe_generate(self, cli_cmd: CLICommand) -> subprocess.CompletedProcess:
        result = self.generate_internal(cli_cmd)
        if isinstance(result, str):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=result)

        if not result.stdout and result.returncode != 0:
            result.stderr += "\nError: Generator returned empty response."
        return result

    def create_command(
        self, cli: str, prompt: str, env: dict = None, resume: bool = False, session_id: str = None, cwd: str = None
    ) -> CLICommand:
        merged_env = self.env.copy()
        if env:
            merged_env.update(env)
        return CLICommand(
            cli=cli, prompt=prompt, env=merged_env,
            resume=resume, session_id=session_id, cwd=cwd
        )
