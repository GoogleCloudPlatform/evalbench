from .generator import QueryGenerator
import subprocess
import os
import json
import logging
import re
import sys
import threading
import time


_SECRET_MANAGER_PATH_RE = re.compile(
    r"^projects/[^/]+/secrets/[^/]+/versions/\d+$"
)
_SECRET_MANAGER_URL_PREFIX = "secret_manager://"


def _strip_sm_prefix(value: str) -> str:
    if value.startswith(_SECRET_MANAGER_URL_PREFIX):
        return value[len(_SECRET_MANAGER_URL_PREFIX):]
    return value


def _looks_like_secret_manager_path(value: str) -> bool:
    if not isinstance(value, str):
        return False
    return bool(_SECRET_MANAGER_PATH_RE.match(_strip_sm_prefix(value)))


def _fetch_secret_manager(path: str) -> str:
    """Fetches a Secret Manager payload via the repo's shared helper.

    Accepts either the bare `projects/.../secrets/.../versions/<N>` form or
    the `secret_manager://projects/.../secrets/.../versions/<N>` URL form.
    The underlying helper requires a numeric version (no `latest`).
    """
    from databases.util import get_db_secret
    return get_db_secret(_strip_sm_prefix(path))


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, session_id=None):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.session_id = session_id


class CodexCliGenerator(QueryGenerator):
    """Generator queries using OpenAI Codex CLI (`codex exec`)."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "codex_cli"

        self.real_home = os.environ.get("HOME", os.path.expanduser("~"))

        if sys.argv[0].endswith("eval_server.py"):
            session_id = querygenerator_config.get("session_id", "default")
            self.fake_home = os.path.join(
                "/tmp_sessions", session_id, "fake_home")
        else:
            self.fake_home = os.path.abspath(
                os.path.join(".venv", "fake_home_codex"))

        self.codex_config_dir = os.path.join(self.fake_home, ".codex")

        os.makedirs(self.fake_home, exist_ok=True)
        os.makedirs(self.codex_config_dir, exist_ok=True)

        self.env = querygenerator_config.get("env", {})
        self.env["HOME"] = self.fake_home

        api_key = self._resolve_openai_api_key(querygenerator_config)
        if api_key:
            self.env["OPENAI_API_KEY"] = api_key
            self._write_codex_auth_json(api_key)
            logging.info(
                "Codex API key resolved (length=%d) and written to %s",
                len(api_key), os.path.join(self.codex_config_dir, "auth.json"),
            )
        else:
            logging.warning(
                "Codex API key could not be resolved; Codex will fall back to "
                "ChatGPT-OAuth and will fail with 402 'deactivated_workspace' "
                "on accounts without ChatGPT-Plus."
            )

        self.codex_cli_version = querygenerator_config.get(
            "codex_cli_version", "codex"
        )
        self.model = querygenerator_config.get("model")
        self.sandbox_mode = querygenerator_config.get(
            "sandbox_mode", "danger-full-access")
        self.approval_mode = querygenerator_config.get(
            "approval_mode", "never")
        self.profile = querygenerator_config.get("profile")
        # Codex emits NDJSON events when invoked with --json. Older versions
        # only support --experimental-json; this flag controls which is used.
        self.json_flag = querygenerator_config.get("json_flag", "--json")

        self.pricing = self._normalize_pricing(querygenerator_config.get("pricing"))

        self.setup_config = querygenerator_config.get("setup", {})
        self.config_path = os.path.join(self.codex_config_dir, "config.toml")
        self._setup()

    @staticmethod
    def _normalize_pricing(pricing) -> dict | None:
        """Normalizes a `pricing:` YAML block into per-token USD rates.

        Accepts either:
          pricing:
            input_per_million_usd:        1.25
            cached_input_per_million_usd: 0.125  # optional; default = 10% of input
            output_per_million_usd:       10.0
        ...or the per-token form (input_per_token_usd, output_per_token_usd, etc.).

        Returns None if pricing is missing or malformed; downstream code then
        leaves cost_usd at 0 instead of guessing.
        """
        if not isinstance(pricing, dict):
            return None

        def _rate(per_million_key: str, per_token_key: str) -> float | None:
            pm = pricing.get(per_million_key)
            pt = pricing.get(per_token_key)
            if pm is not None:
                return float(pm) / 1_000_000.0
            if pt is not None:
                return float(pt)
            return None

        try:
            input_rate = _rate("input_per_million_usd", "input_per_token_usd")
            output_rate = _rate("output_per_million_usd", "output_per_token_usd")
            cached_rate = _rate(
                "cached_input_per_million_usd", "cached_input_per_token_usd",
            )
        except (TypeError, ValueError) as e:
            logging.warning(f"Invalid Codex pricing config; cost_usd will be 0: {e}")
            return None

        if input_rate is None or output_rate is None:
            logging.warning(
                "Codex pricing config missing input/output rates; cost_usd will be 0."
            )
            return None
        if cached_rate is None:
            cached_rate = input_rate * 0.1

        return {
            "input": input_rate,
            "cached_input": cached_rate,
            "output": output_rate,
        }

    def _compute_cost_usd(
        self, input_tokens: int, cached_tokens: int, output_tokens: int,
    ) -> float:
        if not self.pricing:
            return 0.0
        billable_input = max(0, input_tokens - cached_tokens)
        return (
            billable_input * self.pricing["input"]
            + cached_tokens * self.pricing["cached_input"]
            + output_tokens * self.pricing["output"]
        )

    def _resolve_openai_api_key(self, config: dict) -> str:
        """Resolves the OpenAI API key.

        Resolution order:
          1. `openai_api_key_secret` config field — a Secret Manager resource
             path (`projects/.../secrets/.../versions/{N|latest}`), optionally
             prefixed with `secret_manager://`.
          2. `env.OPENAI_API_KEY` (or `OPENAI_API_KEY` from the process env).
             If the value itself looks like a Secret Manager path, it's
             resolved transparently.
        """
        secret_path = config.get("openai_api_key_secret")
        if secret_path:
            try:
                return _fetch_secret_manager(secret_path)
            except Exception as e:
                logging.error(
                    f"Failed to fetch OPENAI_API_KEY from Secret Manager "
                    f"({secret_path}): {e}"
                )
                return ""

        raw = self.env.get("OPENAI_API_KEY") or os.environ.get("OPENAI_API_KEY")
        if raw and _looks_like_secret_manager_path(raw):
            try:
                return _fetch_secret_manager(raw)
            except Exception as e:
                logging.error(
                    f"OPENAI_API_KEY looked like a Secret Manager path but "
                    f"could not be fetched: {e}"
                )
                return ""
        return raw or ""

    _DEFAULT_TOP_LEVEL_CONFIG = {
        "forced_login_method": "api",
    }

    def _write_codex_auth_json(self, api_key: str):
        """Writes ~/.codex/auth.json with the API key.

        Codex's auth manager only honors the `OPENAI_API_KEY` env var when
        `enable_codex_api_key_env` is set internally — for `codex exec` the
        canonical path is `auth.json` (the same file `codex login --api-key`
        produces). Schema (from codex-rs/login/src/auth/storage.rs):

            {"auth_mode": "apikey", "OPENAI_API_KEY": "<key>"}
        """
        auth_path = os.path.join(self.codex_config_dir, "auth.json")
        payload = {"auth_mode": "apikey", "OPENAI_API_KEY": api_key}
        with open(auth_path, "w") as f:
            json.dump(payload, f)
        try:
            os.chmod(auth_path, 0o600)
        except OSError as e:
            logging.warning(
                f"Failed to set permissions on {auth_path} to 0o600: {e}"
            )

    def _setup(self):
        """Performs initial setup for Codex CLI (writes ~/.codex/config.toml)."""
        mcp_servers_config = self.setup_config.get("mcp_servers", {})
        extra_config = dict(self._DEFAULT_TOP_LEVEL_CONFIG)
        extra_config.update(self.setup_config.get("config", {}))
        self._write_config_toml(mcp_servers_config, extra_config)

    def _write_config_toml(self, mcp_servers_config: dict, extra_config: dict):
        """Writes Codex CLI's `config.toml` with MCP server declarations.

        Accepts the same Gemini-style MCP shape the rest of evalbench uses
        (`httpUrl`, `authProviderType: google_credentials`, `headers`) and
        translates it into Codex's TOML schema:

          [mcp_servers.NAME]              # stdio
          command = "..."
          args    = [...]
          env     = { KEY = "VALUE" }

          [mcp_servers.NAME]              # streamable HTTP
          url          = "..."
          http_headers = { KEY = "VALUE" }
        """
        lines: list[str] = []

        for key, value in extra_config.items():
            lines.append(f"{key} = {self._toml_value(value)}")
        if extra_config:
            lines.append("")

        for server_name, config in mcp_servers_config.items():
            translated = self._translate_mcp_config(server_name, dict(config))
            lines.append(f"[mcp_servers.{self._toml_key(server_name)}]")
            for key, value in translated.items():
                lines.append(f"{key} = {self._toml_value(value)}")
            lines.append("")

        with open(self.config_path, "w") as f:
            f.write("\n".join(lines).rstrip() + "\n")

        logging.info(f"Codex CLI config written to {self.config_path}")

    def _translate_mcp_config(self, server_name: str, config: dict) -> dict:
        """Translates a Gemini-style MCP server config into Codex's TOML shape."""
        if "command" in config:
            out = {"command": config["command"]}
            if "args" in config:
                out["args"] = config["args"]
            if "env" in config:
                out["env"] = config["env"]
            if "cwd" in config:
                out["cwd"] = config["cwd"]
            return out

        # HTTP/streamable server: translate Gemini-style `httpUrl` -> `url`
        url = config.get("url") or config.get("httpUrl")
        if not url:
            logging.warning(
                f"MCP server '{server_name}' has no command or url; skipping translation"
            )
            return config

        out: dict = {"url": url}
        headers = dict(config.get("headers") or {})

        auth_provider = config.get("authProviderType")
        if auth_provider == "google_credentials" and "Authorization" not in headers:
            token = self._fetch_gcloud_access_token()
            if token:
                headers["Authorization"] = f"Bearer {token}"
            else:
                logging.warning(
                    f"MCP server '{server_name}' requires google_credentials but "
                    "failed to fetch access token via `gcloud auth print-access-token`."
                )
        if headers:
            out["http_headers"] = headers
        return out

    def _fetch_gcloud_access_token(self) -> str:
        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                check=True,
                capture_output=True,
                text=True,
            )
            return result.stdout.strip()
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logging.error(f"Failed to retrieve gcloud access token: {e}")
            return ""

    @staticmethod
    def _toml_key(key: str) -> str:
        # Bare TOML keys allow [A-Za-z0-9_-]; quote anything else.
        if all(c.isalnum() or c in "_-" for c in key) and key:
            return key
        return json.dumps(key)

    @classmethod
    def _toml_value(cls, value) -> str:
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, (int, float)):
            return json.dumps(value)
        if isinstance(value, str):
            return json.dumps(value)
        if isinstance(value, list):
            return "[" + ", ".join(cls._toml_value(v) for v in value) + "]"
        if isinstance(value, dict):
            inner = ", ".join(
                f"{cls._toml_key(k)} = {cls._toml_value(v)}"
                for k, v in value.items()
            )
            return "{ " + inner + " }"
        return json.dumps(str(value))

    def generate_internal(self, cli_cmd):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.codex_cli_version, str(cli_cmd))
        return self._run_codex_cli(cli_cmd)

    _EV_ITEM_STARTED = "item.started"
    _EV_ITEM_UPDATED = "item.updated"
    _EV_ITEM_COMPLETED = "item.completed"

    # Codex ThreadItem variants we count as "tool calls" for latency purposes.
    _TOOL_ITEM_KINDS = (
        "mcp_tool_call", "command_execution", "web_search", "file_change",
    )

    def _execute_cli_command(
        self, command: list[str], env: dict[str, str] | None = None,
    ) -> tuple[subprocess.CompletedProcess, dict[str, int]]:
        """Runs the Codex CLI with line-streamed stdout so we can stamp the
        wall-clock time at which each NDJSON event arrives.

        Returns the usual CompletedProcess plus a `{item_id: duration_ms}` map
        for ThreadItem tool calls — measured as the gap between `item.started`
        and the matching `item.completed` for kinds in _TOOL_ITEM_KINDS. Codex
        events themselves carry no timestamps, so this in-process stamping is
        the only path to per-tool latency.
        """
        try:
            proc = subprocess.Popen(
                command, env=env, text=True,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                bufsize=1,  # line-buffered so events arrive as Codex flushes them
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                command, 127, "", f"Error: Command not found: {command[0]}"
            ), {}
        except Exception as e:
            return subprocess.CompletedProcess(
                command, 1, "", f"An unexpected error occurred: {e}"
            ), {}

        stderr_chunks: list[str] = []

        def _drain_stderr():
            try:
                for line in proc.stderr:
                    stderr_chunks.append(line)
            except Exception as e:
                logging.debug(f"stderr drain failed: {e}")

        stderr_thread = threading.Thread(target=_drain_stderr, daemon=True)
        stderr_thread.start()

        stdout_lines: list[str] = []
        started_at_ms: dict[str, float] = {}
        tool_durations: dict[str, int] = {}

        try:
            for line in proc.stdout:
                arrival_ms = time.monotonic() * 1000
                stdout_lines.append(line)
                self._stamp_tool_event(
                    line, arrival_ms, started_at_ms, tool_durations,
                )
        except Exception as e:
            logging.warning(f"stdout stream read failed: {e}")

        proc.wait()
        stderr_thread.join(timeout=5)

        completed = subprocess.CompletedProcess(
            command, proc.returncode,
            "".join(stdout_lines), "".join(stderr_chunks),
        )
        return completed, tool_durations

    @classmethod
    def _stamp_tool_event(
        cls, line: str, arrival_ms: float,
        started_at_ms: dict[str, float], tool_durations: dict[str, int],
    ) -> None:
        """If `line` is an `item.started`/`item.completed` event for a tool
        kind, record its arrival time / compute its duration."""
        line = line.strip()
        if not line:
            return
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            return
        event_type = event.get("type", "")
        if event_type not in (cls._EV_ITEM_STARTED, cls._EV_ITEM_COMPLETED):
            return
        item = event.get("item") or {}
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        kind = item.get("type") or item.get("item_type") or details.get("type") or ""
        if kind not in cls._TOOL_ITEM_KINDS:
            return
        item_id = item.get("id") or details.get("id") or ""
        if not item_id:
            return
        if event_type == cls._EV_ITEM_STARTED:
            started_at_ms.setdefault(item_id, arrival_ms)
        else:  # _EV_ITEM_COMPLETED
            t0 = started_at_ms.pop(item_id, None)
            if t0 is not None:
                tool_durations[item_id] = max(0, int(arrival_ms - t0))

    def _run_codex_cli(self, cli_cmd: CLICommand):
        env = os.environ.copy()
        env.update(self.env)
        env.update(cli_cmd.env)

        # Pin a specific npm version when the spec looks like an npm package.
        cli = cli_cmd.cli
        if cli.startswith("@") or "/" in cli:
            command = ["npm", "exec", "--yes", cli, "--"]
        else:
            command = [cli]

        # `codex exec` runs a single non-interactive turn; `codex exec resume`
        # continues a prior session by id.
        if cli_cmd.resume and cli_cmd.session_id:
            command.extend(["exec", "resume", cli_cmd.session_id])
        else:
            command.append("exec")

        command.append(self.json_flag)

        command.append("--skip-git-repo-check")

        # Disable approvals + sandbox so MCP/tool calls run unattended. The
        # caller controls the strength via sandbox_mode/approval_mode in the
        # model config; the default is full bypass (matches Gemini's --yolo
        # and Claude Code's --dangerously-skip-permissions).
        if self.sandbox_mode == "danger-full-access":
            command.append("--dangerously-bypass-approvals-and-sandbox")
        else:
            command.extend(["--sandbox", self.sandbox_mode])
            if self.approval_mode:
                command.extend(["--ask-for-approval", self.approval_mode])

        if self.model:
            command.extend(["-m", self.model])

        if self.profile:
            command.extend(["--profile", self.profile])

        command.append(cli_cmd.prompt)

        logging.info(f"Running Codex CLI: {' '.join(command)}")

        start_ms = time.monotonic()
        result, tool_durations = self._execute_cli_command(command, env=env)
        duration_ms = int((time.monotonic() - start_ms) * 1000)
        if result.stdout:
            result.stdout = self._parse_stream_json(
                result.stdout,
                duration_ms=duration_ms,
                tool_durations=tool_durations,
            )
        if result.stderr:
            result.stderr = self._scrub_stderr(result.stderr)
        return result

    _STDERR_NOISE_PATTERNS = (
        re.compile(
            r"^\s*\d{4}-\d{2}-\d{2}T[^\s]+Z\s+ERROR\s+codex_core::session:\s+"
            r"failed to record rollout items:\s+thread\s+\S+\s+not found\s*$"
        ),
        re.compile(r"^\s*Reading additional input from stdin\.\.\.\s*$"),
    )

    @classmethod
    def _scrub_stderr(cls, stderr: str) -> str:
        kept = [
            line for line in stderr.splitlines()
            if not any(p.match(line) for p in cls._STDERR_NOISE_PATTERNS)
        ]
        if not kept:
            return ""
        out = "\n".join(kept)
        if stderr.endswith("\n"):
            out += "\n"
        return out

    def _parse_stream_json(
        self, stream_output: str,
        duration_ms: int = 0,
        tool_durations: dict[str, int] | None = None,
    ) -> str:
        """Parses Codex CLI ThreadEvent NDJSON into the eval pipeline's
        normalized {session_id, response, stats} shape.

        `duration_ms` is the wall-clock time of the codex subprocess; it gets
        attached to `stats.models.<m>.api.totalLatencyMs` for the
        end_to_end_latency scorer.

        `tool_durations` maps ThreadItem id -> wall-clock ms measured between
        the in-process arrival of `item.started` and `item.completed` (Codex's
        own events carry no timestamps). It's used to populate per-tool
        `byName.<tool>.durationMs` and `tools.totalDurationMs` for the
        tool_call_latency scorer.
        """
        tool_durations = tool_durations or {}

        final_obj = {"session_id": "", "response": "", "stats": {}}
        tool_uses: dict[str, dict] = {}
        tool_results: dict[str, dict] = {}
        usage: dict = {}
        model_name = self.model or "unknown"

        def item_kind(item: dict) -> str:
            return (
                item.get("type")
                or item.get("item_type")
                or (item.get("details") or {}).get("type")
                or ""
            )

        def item_payload(item: dict) -> dict:
            # When the variant is nested under `details`, the payload lives there.
            details = item.get("details")
            if isinstance(details, dict) and details.get("type"):
                return details
            return item

        for line in stream_output.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue

            event_type = event.get("type", "")

            if event_type == "thread.started":
                final_obj["session_id"] = event.get("thread_id", "") or final_obj["session_id"]
                continue

            if event_type == "turn.completed":
                usage = event.get("usage", {}) or usage
                continue

            if event_type == "error":
                # surface the error text into the response field
                msg = event.get("message", "")
                if msg:
                    final_obj["response"] += (
                        ("\n" if final_obj["response"] else "") + f"[error] {msg}"
                    )
                continue

            if event_type not in (
                self._EV_ITEM_STARTED, self._EV_ITEM_UPDATED, self._EV_ITEM_COMPLETED,
            ):
                continue

            item = event.get("item") or {}
            kind = item_kind(item)
            payload = item_payload(item)
            item_id = item.get("id") or payload.get("id") or ""

            if kind == "agent_message":
                if event_type == self._EV_ITEM_COMPLETED:
                    text = payload.get("text", "")
                    if text:
                        final_obj["response"] += text

            elif kind == "mcp_tool_call":
                # Record on first sight; refresh on completion.
                tool_uses[item_id] = {
                    "tool_name": payload.get("tool", "unknown"),
                    "server": payload.get("server", ""),
                    "parameters": self._coerce_json(payload.get("arguments", {})),
                }
                if event_type == self._EV_ITEM_COMPLETED:
                    status = payload.get("status", "")
                    is_error = bool(payload.get("error")) or status not in (
                        "", "completed", "success", "ok",
                    )
                    tool_results[item_id] = {
                        "status": "error" if is_error else "success",
                        "content": payload.get("result", ""),
                    }

            elif kind == "command_execution":
                tool_uses[item_id] = {
                    "tool_name": "shell",
                    "parameters": {"command": payload.get("command", "")},
                }
                if event_type == self._EV_ITEM_COMPLETED:
                    exit_code = payload.get("exit_code")
                    is_error = bool(exit_code) and exit_code != 0
                    tool_results[item_id] = {
                        "status": "error" if is_error else "success",
                        "content": payload.get("aggregated_output", ""),
                    }

            elif kind == "web_search":
                tool_uses[item_id] = {
                    "tool_name": "web_search",
                    "parameters": {"query": payload.get("query", "")},
                }
                if event_type == self._EV_ITEM_COMPLETED:
                    tool_results[item_id] = {"status": "success", "content": ""}

            elif kind == "file_change":
                tool_uses[item_id] = {
                    "tool_name": "file_change",
                    "parameters": {"changes": payload.get("changes", [])},
                }
                if event_type == self._EV_ITEM_COMPLETED:
                    status = payload.get("status", "")
                    is_error = status not in ("", "completed", "success", "ok")
                    tool_results[item_id] = {
                        "status": "error" if is_error else "success",
                        "content": "",
                    }

        input_tokens = int(usage.get("input_tokens", 0) or 0)
        output_tokens = int(usage.get("output_tokens", 0) or 0)
        cached_tokens = int(usage.get("cached_input_tokens", 0) or 0)
        total_tokens = input_tokens + output_tokens
        cost_usd = self._compute_cost_usd(input_tokens, cached_tokens, output_tokens)

        models = {
            model_name: {
                "api": {
                    "totalRequests": 1,
                    "totalErrors": 0,
                    "totalLatencyMs": duration_ms,
                },
                "tokens": {
                    "input": input_tokens,
                    "prompt": input_tokens,
                    "candidates": output_tokens,
                    "total": total_tokens,
                    "cached": cached_tokens,
                    "cache_creation": 0,
                    "thoughts": 0,
                    "tool": 0,
                },
                "cost_usd": cost_usd,
                "roles": {
                    "main": {
                        "totalRequests": 1,
                        "totalErrors": 0,
                        "totalLatencyMs": duration_ms,
                        "tokens": {
                            "input": input_tokens,
                            "prompt": input_tokens,
                            "candidates": output_tokens,
                            "total": total_tokens,
                            "cached": cached_tokens,
                            "thoughts": 0,
                            "tool": 0,
                        },
                    }
                },
            }
        }
        final_obj["stats"]["models"] = models

        total_tool_duration_ms = sum(
            tool_durations.get(tid, 0) for tid in tool_uses
        )
        tools_stats = {
            "totalCalls": len(tool_uses),
            "totalSuccess": sum(
                1 for tr in tool_results.values() if tr.get("status") == "success"
            ),
            "totalFail": sum(
                1 for tr in tool_results.values() if tr.get("status") != "success"
            ),
            "totalDurationMs": total_tool_duration_ms,
            "decisions": {
                "accept": len(tool_uses),
                "reject": 0,
                "modify": 0,
                "auto_accept": len(tool_uses),
            },
            "byName": {},
        }

        for tid, tu in tool_uses.items():
            tname = tu.get("tool_name", "unknown")
            bucket = tools_stats["byName"].setdefault(tname, {
                "count": 0,
                "success": 0,
                "fail": 0,
                "durationMs": 0,
                "parameters": [],
                "decisions": {
                    "accept": 0, "reject": 0, "modify": 0, "auto_accept": 0,
                },
            })
            bucket["count"] += 1
            bucket["durationMs"] += tool_durations.get(tid, 0)
            bucket["parameters"].append(tu.get("parameters", {}))
            bucket["decisions"]["accept"] += 1
            bucket["decisions"]["auto_accept"] += 1

            tr = tool_results.get(tid)
            if tr:
                if tr.get("status") == "success":
                    bucket["success"] += 1
                else:
                    bucket["fail"] += 1

        final_obj["stats"]["tools"] = tools_stats

        return json.dumps(final_obj, indent=2)

    @staticmethod
    def _coerce_json(value):
        """Codex serializes tool arguments as a JSON-encoded string. Parse it
        back into a dict when possible so scorers see real fields."""
        if isinstance(value, str):
            try:
                return json.loads(value)
            except (ValueError, TypeError):
                return {"raw": value}
        return value or {}

    def parse_response(self, stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            logging.error(f"Failed to parse JSON response: {stdout[:100]}...")
            return {}

    def extract_tools(self, stdout: str) -> list[str]:
        output_json = self.parse_response(stdout)
        if (
            "stats" in output_json
            and "tools" in output_json["stats"]
            and "byName" in output_json["stats"]["tools"]
        ):
            return list(output_json["stats"]["tools"]["byName"].keys())
        return []

    def safe_generate(self, cli_cmd: CLICommand) -> subprocess.CompletedProcess:
        result = self.generate_internal(cli_cmd)
        if isinstance(result, str):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=result)
        if not result.stdout and result.returncode != 0:
            result.stderr += "\nError: Generator returned empty response."
        return result

    def create_command(
        self, cli: str, prompt: str, env: dict = None, resume: bool = False,
        session_id: str = None,
    ) -> CLICommand:
        merged_env = self.env.copy()
        if env:
            merged_env.update(env)
        return CLICommand(
            cli=cli, prompt=prompt, env=merged_env,
            resume=resume, session_id=session_id,
        )
