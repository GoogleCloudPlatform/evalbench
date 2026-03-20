from .generator import QueryGenerator
import subprocess
import os
import json
import logging
import re
import shutil
import sys


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, yolo=True):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.yolo = yolo


class GeminiCliGenerator(QueryGenerator):
    """Generator queries using Gemini CLI."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "gemini_cli"

        self.real_home = os.environ.get("HOME", os.path.expanduser("~"))

        # If running via eval_server.py (gRPC), use session-specific path in shared volume
        if sys.argv[0].endswith("eval_server.py"):
            session_id = querygenerator_config.get("session_id", "default")
            self.fake_home = os.path.join("/tmp_sessions", session_id, "fake_home")
        else:
            self.fake_home = os.path.abspath(os.path.join(".venv", "fake_home"))

        self.gemini_home = os.path.join(self.fake_home, ".gemini")
        self.extensions_dir = os.path.join(self.gemini_home, "extensions")
        self.skills_dir = os.path.join(self.gemini_home, "skills")

        os.makedirs(self.fake_home, exist_ok=True)
        os.makedirs(self.extensions_dir, exist_ok=True)
        os.makedirs(self.skills_dir, exist_ok=True)

        self.env = querygenerator_config.get("env", {})
        self.env["HOME"] = self.fake_home

        if "GOOGLE_APPLICATION_CREDENTIALS" not in self.env:
            default_adc = os.path.join(
                self.real_home,
                ".config",
                "gcloud",
                "application_default_credentials.json",
            )
            if os.path.exists(default_adc):
                self.env["GOOGLE_APPLICATION_CREDENTIALS"] = default_adc

        if "CLOUDSDK_CONFIG" not in self.env:
            self.env["CLOUDSDK_CONFIG"] = os.path.join(
                self.real_home, ".config", "gcloud"
            )

        self.gemini_cli_version = querygenerator_config.get(
            "gemini_cli_version", "gemini-cli"
        )
        self.setup_config = querygenerator_config.get("setup", {})
        if self.setup_config:
            self._setup()

    def _setup(self):
        """Performs initial setup for Gemini CLI."""
        gemini_settings_path = os.path.join(self.gemini_home, "settings.json")

        if not os.path.exists(os.path.dirname(gemini_settings_path)):
            os.makedirs(os.path.dirname(gemini_settings_path), exist_ok=True)

        # Setup NPM Authentication first, so we can pull gemini-cli/extensions
        self._setup_npm_auth()

        # Setup MCP Servers
        mcp_servers_config = self.setup_config.get("mcp_servers", {})
        self._setup_mcp_servers(mcp_servers_config, gemini_settings_path)
        if "fake_mcp_servers" in self.setup_config:
            self._setup_mcp_servers(
                self.setup_config["fake_mcp_servers"],
                gemini_settings_path,
                verify_tools=False,
            )

        # Install Extensions
        extensions_config = self.setup_config.get("extensions", {})
        self._install_extensions(extensions_config)

        # Setup Skills
        skills_config = self.setup_config.get("skills", [])
        self._setup_skills(skills_config)

    def _setup_npm_auth(self):
        """Sets up NPM authentication for private registries in the FAKE HOME."""
        logging.info("Fetching new access token via gcloud auth command")

        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                check=True,
                capture_output=True,
                text=True,
            )
            access_token = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to retrieve access token: {e.stderr}")
            return

        if not access_token:
            logging.error(
                "Error: Failed to retrieve access token. Please run 'gcloud auth login' first."
            )
            return

        npmrc_file = os.path.join(self.fake_home, ".npmrc")
        logging.info(f"Updating {npmrc_file} with new token...")

        registries = [
            "//us-west1-npm.pkg.dev/gemini-code-dev/gemini-code/",
            "//us-npm.pkg.dev/artifact-foundry-prod/npm-3p-trusted/",
            "//us-npm.pkg.dev/artifact-foundry-prod/ah-3p-staging-npm/",
        ]

        lines = []
        real_npmrc = os.path.join(self.real_home, ".npmrc")
        if os.path.exists(real_npmrc):
            with open(real_npmrc, "r") as f:
                lines = f.readlines()

        if os.path.exists(npmrc_file):
            with open(npmrc_file, "r") as f:
                lines = f.readlines()

        for registry in registries:
            token_line = f"{registry}:_authToken={access_token}\n"
            auth_line = f"{registry}:always-auth=true\n"

            token_found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{registry}:_authToken="):
                    lines[i] = token_line
                    token_found = True
                    break
            if not token_found:
                lines.append(token_line)

            auth_found = False
            for i, line in enumerate(lines):
                if line.startswith(f"{registry}:always-auth=true"):
                    auth_found = True
                    break
            if not auth_found:
                lines.append(auth_line)

        with open(npmrc_file, "w") as f:
            f.writelines(lines)

        logging.info(f"NPM authentication updated successfully at {npmrc_file}")

    def _setup_skills(self, skills: list):
        """Sets up skills by copying them or performing specified actions."""
        if not skills:
            return

        real_skills_dir = os.path.join(self.real_home, ".gemini", "skills")

        setup_env = os.environ.copy()
        setup_env.update(self.env)

        for skill_config in skills:
            if isinstance(skill_config, str):
                skill_name = skill_config
                real_skill_path = os.path.join(real_skills_dir, skill_name)
                fake_skill_path = os.path.join(self.skills_dir, skill_name)

                if not os.path.exists(real_skill_path):
                    logging.warning(
                        f"Requested skill '{skill_name}' not found at {real_skill_path}."
                    )
                    continue

                logging.info(f"Syncing skill: {skill_name}")
                if os.path.exists(fake_skill_path):
                    shutil.rmtree(fake_skill_path)
                try:
                    shutil.copytree(real_skill_path, fake_skill_path)
                except Exception as e:
                    logging.error(f"Failed to copy skill {skill_name}: {e}")

            elif isinstance(skill_config, dict):
                action = skill_config.get("action")
                path = skill_config.get("path")
                name = skill_config.get("name")

                cmd = None
                if action == "link" and path:
                    logging.info(f"Linking skill from path: {path}")
                    cmd = [
                        "npm",
                        "exec",
                        "--yes",
                        self.gemini_cli_version,
                        "--",
                        "skills",
                        "link",
                        path,
                        "--consent",
                    ]
                elif action == "install" and (path or name):
                    target = path if path else name
                    logging.info(f"Installing skill: {target}")
                    cmd = [
                        "npm",
                        "exec",
                        "--yes",
                        self.gemini_cli_version,
                        "--",
                        "skills",
                        "install",
                        target,
                        "--consent",
                    ]
                elif action == "enable" and name:
                    logging.info(f"Enabling skill: {name}")
                    cmd = [
                        "npm",
                        "exec",
                        "--yes",
                        self.gemini_cli_version,
                        "--",
                        "skills",
                        "enable",
                        name,
                    ]
                elif action == "disable" and name:
                    logging.info(f"Disabling skill: {name}")
                    cmd = [
                        "npm",
                        "exec",
                        "--yes",
                        self.gemini_cli_version,
                        "--",
                        "skills",
                        "disable",
                        name,
                    ]
                elif action == "uninstall" and name:
                    logging.info(f"Uninstalling skill: {name}")
                    cmd = [
                        "npm",
                        "exec",
                        "--yes",
                        self.gemini_cli_version,
                        "--",
                        "skills",
                        "uninstall",
                        name,
                    ]
                else:
                    logging.warning(
                        f"Unsupported or malformed skill config: {skill_config}"
                    )

                if cmd:
                    try:
                        result = subprocess.run(
                            cmd,
                            check=False,
                            capture_output=True,
                            text=True,
                            env=setup_env,
                        )
                        if result.returncode != 0:
                            logging.error(
                                f"Failed to execute skill action '{action}'. Output: {result.stdout}, Error: {result.stderr}"
                            )
                    except Exception as e:
                        logging.error(f"Failed to execute skill action '{action}': {e}")

    def _setup_mcp_servers(
        self, mcp_servers_config: dict, settings_path: str, verify_tools: bool = True
    ):
        """Configures MCP servers in the settings file and verifies connectivity."""
        current_settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f:
                    current_settings = json.load(f)
            except json.JSONDecodeError:
                pass

        if "mcpServers" not in current_settings:
            current_settings["mcpServers"] = {}

        existing_servers = list(current_settings["mcpServers"].keys())
        for server in existing_servers:
            if server not in mcp_servers_config:
                logging.info(f"Removing stale MCP server configuration: {server}")
                del current_settings["mcpServers"][server]

        for server_name, config in mcp_servers_config.items():
            current_settings["mcpServers"][server_name] = config

        with open(settings_path, "w") as f:
            json.dump(current_settings, f, indent=2)

        if verify_tools:
            for server_name, config in mcp_servers_config.items():
                logging.info(f"Verifying MCP server: {server_name}")
                if not self._verify_mcp_server(server_name, settings_path):
                    raise RuntimeError(
                        f"MCP Server '{server_name}' failed verification. Please check the configuration and ensure the server is running correctly."
                    )

    def _verify_mcp_server(self, server_name: str, settings_path: str) -> bool:
        """Verifies an MCP server by asking the Gemini model CLI what tools it has loaded."""

        verify_env = os.environ.copy()
        verify_env.update(self.env)
        verify_env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = settings_path

        prompt = "List the exact names of all tools provided to you. Return ONLY a JSON array of their names. Do not include markdown formatting or backticks."
        cmd = [
            "npm",
            "exec",
            "--yes",
            self.gemini_cli_version,
            "--",
            "run",
            prompt,
            "--output-format",
            "json",
        ]

        if hasattr(self, "model") and isinstance(self.model, str):
            cmd.extend(["--model", self.model])

        cmd.extend(["--allowed-mcp-server-names", server_name])

        logging.info(
            f"Running gemini cli to verify loaded tools for MCP server: {server_name}"
        )

        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                env=verify_env,
                timeout=300,
            )

            if result.returncode != 0:
                logging.error(
                    f"MCP server '{server_name}' failed verification. CLI Error:\n{result.stderr}"
                )
                return False

            stdout = result.stdout.strip()

            try:
                json_start = stdout.find("{")
                json_end = stdout.rfind("}") + 1
                if json_start >= 0 and json_end > json_start:
                    envelope = json.loads(stdout[json_start:json_end])
                    if "response" in envelope:
                        response_text = envelope["response"].strip()

                        if response_text.startswith('```'):
                            lines = response_text.split('\n')
                            if lines and lines[0].startswith('```'):
                                lines = lines[1:]
                            if lines and lines[-1].startswith('```'):
                                lines = lines[:-1]
                            response_text = '\n'.join(lines).strip()

                        tools = json.loads(response_text)

                        if isinstance(tools, list):
                            # Filter out standard Gemini CLI built-in tools
                            built_in_tools = {
                                "list_directory",
                                "read_file",
                                "search_file_content",
                                "glob",
                                "activate_skill",
                                "save_memory",
                                "google_web_search",
                                "write_todos",
                                "delegate_to_agent",
                                "grep_search",
                                "codebase_investigator",
                                "cli_help",
                            }
                            mcp_tools = [t for t in tools if t not in built_in_tools]

                            if len(mcp_tools) > 0:
                                logging.info(
                                    f"MCP server '{server_name}' successfully loaded {len(mcp_tools)} tools: {mcp_tools}"
                                )
                                return True
                            else:
                                logging.error(
                                    f"MCP server '{server_name}' returned 0 non-builtin tools. The server might be unreachable or lacks tools."
                                )
                                return False
            except Exception as e:
                logging.debug(
                    f"Failed to parse tools from MCP server {server_name}: {e}"
                )

            logging.error(
                f"MCP server '{server_name}' didn't return a clear JSON array. Output: {stdout}"
            )
            return False

        except subprocess.TimeoutExpired:
            logging.error(f"Verification of MCP server {server_name} timed out.")
            return False
        except Exception as e:
            logging.error(f"Failed to verify MCP server {server_name}: {e}")
            return False

    def _install_extensions(self, extensions: dict | list):
        """Installs/Syncs specified extensions using gemini-cli."""
        if isinstance(extensions, list):
            extensions = {ext: {} for ext in set(extensions)}

        extension_names = sorted(list(extensions.keys()))

        install_env = os.environ.copy()
        install_env.update(self.env)

        installed_extensions = set()
        try:
            cmd = [
                "npm",
                "exec",
                "--yes",
                self.gemini_cli_version,
                "--",
                "extensions",
                "list",
            ]
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False, env=install_env
            )

            for line in result.stdout.splitlines():
                line = line.strip()

                warn_match = re.search(
                    r"Warning: Skipping extension in (.*?): Configuration file not found",
                    line,
                )
                if warn_match:
                    corrupted_path = warn_match.group(1).strip()
                    logging.warning(
                        f"Detected corrupted extension at {corrupted_path}. Removing..."
                    )
                    try:
                        shutil.rmtree(corrupted_path)
                    except Exception as e:
                        logging.error(
                            f"Failed to remove corrupted extension directory {corrupted_path}: {e}"
                        )
                    continue

                keychain_match = re.search(
                    r"Warning: Skipping extension in (.*?): Keychain is not available",
                    line,
                )
                if keychain_match:
                    ext_path = keychain_match.group(1).strip()
                    self._patch_manifest_sensitive(ext_path)

                    name_match = re.search(r"extensions/([^/]+)$", ext_path)
                    if name_match:
                        installed_extensions.add(name_match.group(1))
                    continue

                if not line or line.startswith(("Source:", "Path:", "ID:", "name:")):
                    continue

                if "✓" in line or ("(" in line and ")" in line):
                    parts = line.split()
                    if len(parts) >= 2:
                        if "(" in parts[0]:
                            continue

                        name = (
                            parts[1]
                            if "✓" in parts[0] and len(parts) >= 2
                            else parts[0]
                        )

                        if not name.startswith("("):
                            installed_extensions.add(name)

        except Exception as e:
            logging.warning(f"Failed to list extensions: {e}")

        # Uninstall extraneous extensions
        to_uninstall = []
        for ext_name in installed_extensions:
            keep = False
            for req in extension_names:
                if ext_name in req:
                    keep = True
                    break
            if not keep:
                to_uninstall.append(ext_name)

        if to_uninstall:
            logging.info(f"Uninstalling extraneous extensions: {to_uninstall}")
            for ext in to_uninstall:
                try:
                    subprocess.run(
                        [
                            "npm",
                            "exec",
                            "--yes",
                            self.gemini_cli_version,
                            "--",
                            "extensions",
                            "uninstall",
                            ext,
                        ],
                        check=False,
                        capture_output=True,
                        env=install_env,
                    )
                except Exception as e:
                    logging.warning(f"Failed to uninstall extension {ext}: {e}")

        # Install requested extensions
        for ext in extension_names:
            already_installed = False
            for installed in installed_extensions:
                if installed == ext:
                    already_installed = True
                    break
                if "/" in ext and ext.rstrip("/").rstrip(".git").endswith(installed):
                    already_installed = True
                    break

            if already_installed:
                logging.info(f"Extension '{ext}' appears to be already installed. Skipping.")
                continue

            logging.info(f"Installing extension: {ext}")

            current_ext_env = install_env.copy()
            if extensions[ext] and "settings" in extensions[ext]:
                current_ext_env.update(extensions[ext]["settings"])

            try:
                # gemini extensions install <name_or_url> --consent
                result = subprocess.run(
                    [
                        "npm",
                        "exec",
                        "--yes",
                        self.gemini_cli_version,
                        "--",
                        "extensions",
                        "install",
                        ext,
                        "--consent",
                    ],
                    check=False,
                    capture_output=True,
                    text=True,
                    input="\n" * 10,
                    env=current_ext_env,
                    timeout=300,
                )

                if result.returncode != 0:
                    logging.error(
                        f"Failed to install extension {ext}. Output: {result.stdout}, Error: {result.stderr}"
                    )
                else:
                    logging.info(f"Successfully installed extension: {ext}")

                ext_name_match = re.search(r"([^/]+?)(?:\.git)?$", ext)
                if ext_name_match:
                    search_name = ext_name_match.group(1)
                    if os.path.exists(self.extensions_dir):
                        for item in os.listdir(self.extensions_dir):
                            if search_name in item:
                                self._patch_manifest_sensitive(
                                    os.path.join(self.extensions_dir, item)
                                )

            except subprocess.TimeoutExpired:
                logging.error(f"Installation of extension {ext} timed out.")
            except Exception as e:
                logging.error(f"Failed to install extension {ext}: {e}")

    def _patch_manifest_sensitive(self, ext_path):
        """Patches extension manifest to disable keychain requirements."""
        manifest_path = os.path.join(ext_path, "gemini-extension.json")
        try:
            if os.path.exists(manifest_path):
                with open(manifest_path, "r") as f:
                    content = f.read()

                if '"sensitive": true' in content:
                    logging.info(
                        f"Patching manifest at {manifest_path} for headless compatibility."
                    )
                    content = content.replace('"sensitive": true', '"sensitive": false')
                    with open(manifest_path, "w") as f:
                        f.write(content)
        except Exception as e:
            logging.error(f"Failed to patch manifest at {manifest_path}: {e}")

    def generate_internal(self, cli_cmd: CLICommand | str):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.gemini_cli_version, str(cli_cmd))
        return self._run_gemini_cli(cli_cmd)

    def _execute_cli_command(
        self, command: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(command, capture_output=True, text=True, check=False, env=env)
            # Filter out benign schema warnings from json decoder from stderr to reduce noise
            if result.stderr:
                result.stderr = "\n".join(
                    [
                        line
                        for line in result.stderr.splitlines()
                        if 'unknown format "google-duration" ignored' not in line
                    ]
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

    def _run_gemini_cli(self, cli_cmd: CLICommand):
        gemini_settings_path = os.path.join(self.gemini_home, "settings.json")

        if not os.path.exists(gemini_settings_path):
            os.makedirs(os.path.dirname(gemini_settings_path), exist_ok=True)
            with open(gemini_settings_path, "w") as f:
                json.dump({}, f)

        env = os.environ.copy()
        env.update(self.env)
        env.update(cli_cmd.env)

        env["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = gemini_settings_path

        command = [
            "npm",
            "exec",
            "--yes",
            cli_cmd.cli,
            "--",
        ]
        if cli_cmd.resume:
            command.append("--resume")
        if cli_cmd.yolo:
            command.append("--yolo")

        command.extend(
            [
                "--output-format",
                "stream-json",
                cli_cmd.prompt,
            ]
        )

        result = self._execute_cli_command(command, env=env)
        if result.returncode == 0 and result.stdout:
            result.stdout = self._parse_stream_json(result.stdout)

        return result

    def _parse_stream_json(self, stream_output: str) -> str:
        import dateutil.parser

        final_obj = {"session_id": "", "response": "", "stats": {}}
        tool_uses = {}
        tool_results = {}
        model_name = "gemini-2.5-flash"

        for line in stream_output.split("\n"):
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
                    tool_id = event.get("tool_id")
                    if tool_id:
                        tool_uses[tool_id] = event
                elif t == "tool_result":
                    tool_id = event.get("tool_id")
                    if tool_id:
                        tool_results[tool_id] = event
                elif t == "result":
                    s = event.get("stats", {})
                    total_duration = s.get("duration_ms", 0)

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
                            if tr.get("status") == "success"
                        ),
                        "totalFail": sum(
                            1
                            for tr in tool_results.values()
                            if tr.get("status") != "success"
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
                        tname = tu.get("tool_name", "unknown")
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
                        tstat["parameters"].append(tu.get("parameters", {}))
                        tstat["decisions"]["accept"] += 1
                        tstat["decisions"]["auto_accept"] += 1

                        tr = tool_results.get(tid)
                        duration = 0
                        if tr:
                            if tr.get("status") == "success":
                                tstat["success"] += 1
                            else:
                                tstat["fail"] += 1

                            try:
                                t1 = dateutil.parser.isoparse(tu["timestamp"])
                                t2 = dateutil.parser.isoparse(tr["timestamp"])
                                duration = int((t2 - t1).total_seconds() * 1000)
                            except Exception as e:
                                logging.debug(
                                    "Failed to parse tool timestamps for duration calculation: "
                                    f"tool_use_ts={tu.get('timestamp')!r}, "
                                    f"tool_result_ts={tr.get('timestamp')!r}, error={e}"
                                )

                        tstat["durationMs"] += duration
                        tools_stats["totalDurationMs"] += duration

                    final_obj["stats"]["tools"] = tools_stats
            except Exception as e:
                logging.debug(f"Failed to parse stream JSON line: {e}")

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

    def safe_generate(self, cli_cmd: CLICommand) -> subprocess.CompletedProcess:
        result = self.generate_internal(cli_cmd)
        if isinstance(result, str):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=result)

        if not result.stdout and result.returncode != 0:
            result.stderr += "\nError: Generator returned empty response."
        return result

    def create_command(
        self, cli: str, prompt: str, env: dict = None, resume: bool = False
    ) -> CLICommand:
        merged_env = self.env.copy()

        if hasattr(self, "setup_config") and "extensions" in self.setup_config:
            for _, ext_data in self.setup_config["extensions"].items():
                if ext_data and "settings" in ext_data:
                    merged_env.update(ext_data["settings"])

        if env:
            merged_env.update(env)
        return CLICommand(cli=cli, prompt=prompt, env=merged_env, resume=resume)
