from .agent_cli import AgentCliGenerator
from .tool_naming import canonicalize_claude_tool_name
import subprocess
import os
import json
import logging
import shlex
import sys
import re
import shutil
from typing import Optional, Union, Dict, List
from util.context import rpc_id_var


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, session_id=None, allowedTools=None, cwd=None):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.session_id = session_id
        self.allowedTools = allowedTools
        self.cwd = cwd


class ClaudeCodeGenerator(AgentCliGenerator):
    """Generator queries using Claude Code CLI."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "claude_code"

        self.real_home = os.environ.get("HOME", os.path.expanduser("~"))

        # If running via eval_server.py (gRPC), use session-specific path in shared volume
        if sys.argv[0].endswith("eval_server.py"):
            session_id = querygenerator_config.get("session_id")
            if not session_id:
                ctx_id = rpc_id_var.get()
                session_id = ctx_id if ctx_id != "default" else "default"
            self.fake_home = os.path.join(
                "/tmp_sessions", session_id, "fake_home")
        else:
            self.fake_home = os.path.abspath(
                os.path.join(".venv", "fake_home_claude"))

        self.claude_config_dir = os.path.join(self.fake_home, ".claude")

        os.makedirs(self.fake_home, exist_ok=True)
        os.makedirs(self.claude_config_dir, exist_ok=True)

        # When running as root, chown fake_home so the non-root claudeuser
        # (used to run Claude Code) can write to it.
        self._chown_for_claudeuser = os.getuid() == 0

        self.env = querygenerator_config.get("env") or {}
        self.env["HOME"] = self.fake_home
        self.env["IS_SANDBOX"] = "1"

        api_key = self.env.get("ANTHROPIC_API_KEY") or os.environ.get("ANTHROPIC_API_KEY")

        # Ignore unfilled template placeholders (e.g. "<API_KEY>"). Left as-is
        # they are truthy, so they silently disable Vertex (use_vertex: true)
        # and make Claude Code fail with "Invalid API key".
        if api_key and api_key.strip().startswith("<"):
            logging.warning(
                f"Ignoring placeholder ANTHROPIC_API_KEY value {api_key!r}.")
            self.env.pop("ANTHROPIC_API_KEY", None)
            api_key = None

        if api_key:
            self.env["ANTHROPIC_API_KEY"] = api_key
            self.use_vertex = False
            self.env.pop("CLAUDE_CODE_USE_VERTEX", None)
        else:
            self.use_vertex = querygenerator_config.get("use_vertex", False)
            if self.use_vertex:
                self.env["CLAUDE_CODE_USE_VERTEX"] = "1"
                vertex_project = querygenerator_config.get(
                    "vertex_project_id"
                ) or self.env.get("ANTHROPIC_VERTEX_PROJECT_ID") or os.environ.get("ANTHROPIC_VERTEX_PROJECT_ID")
                if vertex_project:
                    self.env["ANTHROPIC_VERTEX_PROJECT_ID"] = vertex_project

                vertex_region = querygenerator_config.get(
                    "vertex_region"
                ) or self.env.get("CLOUD_ML_REGION") or os.environ.get("CLOUD_ML_REGION")
                if vertex_region:
                    self.env["CLOUD_ML_REGION"] = vertex_region

                # Skip ADC setup if Service Account key is available
                if not os.path.exists("/etc/evalbench-sa-key/key.json"):
                    fake_gcloud_dir = os.path.join(
                        self.fake_home, ".config", "gcloud")

                    # If GOOGLE_APPLICATION_CREDENTIALS is already set (e.g.,
                    # Cloud Run mounts ADC at a specific path), copy that single
                    # file into the fake home and point the env var at it.
                    explicit_adc = self.env.get("GOOGLE_APPLICATION_CREDENTIALS")
                    if (
                        explicit_adc
                        and os.path.exists(explicit_adc)
                        and not explicit_adc.startswith("/etc/")
                    ):
                        os.makedirs(fake_gcloud_dir, exist_ok=True)
                        fake_adc_path = os.path.join(
                            fake_gcloud_dir, "application_default_credentials.json")
                        if os.path.abspath(explicit_adc) != os.path.abspath(fake_adc_path):
                            shutil.copy2(explicit_adc, fake_adc_path)
                        self.env["GOOGLE_APPLICATION_CREDENTIALS"] = fake_adc_path
                    else:
                        # Local dev: copy the whole gcloud config dir so fresh
                        # `gcloud auth login` credentials (credentials.db,
                        # access_tokens.db, ADC) are available to skill scripts.
                        real_gcloud_dir = os.path.join(
                            self.real_home, ".config", "gcloud")
                        if os.path.exists(real_gcloud_dir):
                            os.makedirs(os.path.dirname(fake_gcloud_dir), exist_ok=True)
                            if os.path.exists(fake_gcloud_dir):
                                shutil.rmtree(fake_gcloud_dir)
                            try:
                                shutil.copytree(
                                    real_gcloud_dir, fake_gcloud_dir,
                                    ignore=shutil.ignore_patterns('logs', '*.db-wal'),
                                )
                                logging.info(
                                    f"Copied gcloud config from {real_gcloud_dir} to {fake_gcloud_dir}")
                            except (OSError, PermissionError) as e:
                                logging.warning(f"Could not copy gcloud directory: {e}")
                        fake_adc_path = os.path.join(
                            fake_gcloud_dir, "application_default_credentials.json")
                        if os.path.exists(fake_adc_path) and "GOOGLE_APPLICATION_CREDENTIALS" not in self.env:
                            self.env["GOOGLE_APPLICATION_CREDENTIALS"] = fake_adc_path

                    if "CLOUDSDK_CONFIG" not in self.env:
                        self.env["CLOUDSDK_CONFIG"] = fake_gcloud_dir
                else:
                    # Explicitly set GOOGLE_APPLICATION_CREDENTIALS for Claude if secret is mounted
                    self.env["GOOGLE_APPLICATION_CREDENTIALS"] = "/etc/evalbench-sa-key/key.json"

        # Copy Claude Code auth credentials from real home to fake home so the
        # CLI can authenticate in the sandboxed environment. Restricted to a
        # tight allowlist of auth-only files: copying everything (e.g.
        # settings.json, CLAUDE.md) silently overrides the eval's model config
        # — settings.json `env`, `model`, and `enabledPlugins` would leak the
        # local developer's setup into the run.
        _AUTH_FILE_ALLOWLIST = {".credentials.json"}
        real_claude_dir = os.path.join(self.real_home, ".claude")
        if os.path.exists(real_claude_dir):
            for fname in _AUTH_FILE_ALLOWLIST:
                src = os.path.join(real_claude_dir, fname)
                dst = os.path.join(self.claude_config_dir, fname)
                if os.path.isfile(src) and not os.path.exists(dst):
                    shutil.copy2(src, dst)

        self.claude_code_version = querygenerator_config.get(
            "claude_code_version", "claude"
        )
        self.model = querygenerator_config.get("model")
        self.allowed_tools = querygenerator_config.get("allowed_tools")

        self.setup_config = querygenerator_config.get("setup", {})
        if self.setup_config:
            self._setup()

    def _setup(self):
        """Performs initial setup for Claude Code CLI.

        Skills can be set up in two ways:
        1. install_from_repo: Clone a plugin marketplace from a git repo
           Example: {action: "install_from_repo", url: "https://github.com/repo.git#v1.0.0"}
        2. skills_dir: Use a local directory as a plugin marketplace
           Example: skills_dir: "/path/to/plugin-marketplace"

        Both register the marketplace in settings.json as a `directory` source
        and enable its first plugin via `enabledPlugins`, so Claude Code loads
        the skills automatically without interactive `/plugin install`.
        """
        mcp_servers_config = self.setup_config.get("mcp_servers", {})
        if mcp_servers_config:
            self._setup_mcp_servers(mcp_servers_config)

        settings_config = self.setup_config.get("settings", {})
        if settings_config:
            self._setup_settings(settings_config)

        skills_config = self.setup_config.get("skills", [])
        if skills_config:
            self._setup_skills(skills_config)

        skills_dir_path = self.setup_config.get("skills_dir")
        if skills_dir_path:
            self._setup_skills_from_dir(skills_dir_path)

    def _setup_mcp_servers(self, mcp_servers_config):
        """Configures MCP servers for Claude Code.

        Two forms are supported:

        1. Inline servers (dict): ``{server_name: config, ...}``. Written to
           ``mcp_servers.json`` and passed via ``--mcp-config``. Supports the
           same config shape as Gemini CLI for HTTP MCP servers:
             httpUrl:           URL (translated to Claude Code's `url` + `type: "http"`)
             authProviderType:  "google_credentials" -> injects `gcloud auth print-access-token` as Bearer
             headers:           passed through as-is
           Stdio servers (command/args) are passed through unchanged.

        2. Install from repo (list): ``[{action: "install_from_repo", url: ...}, ...]``.
           Clones a Claude Code plugin marketplace repo whose plugin bundles MCP
           servers (`.mcp.json` / `mcpServers` in plugin.json) and enables it in
           settings.json. Claude Code starts the bundled MCP servers
           automatically when the plugin is enabled -- mirroring the `skills`
           install_from_repo flow. Each entry may also carry `plugin` (enable a
           specific plugin instead of the marketplace's first) and `config`
           (values for the plugin's `userConfig`, so `${user_config.*}`
           placeholders in the bundled MCP config resolve non-interactively).
        """
        if isinstance(mcp_servers_config, list):
            self._install_mcp_servers_from_repo(mcp_servers_config)
            return

        mcp_config = {"mcpServers": {}}

        for server_name, config in mcp_servers_config.items():
            mcp_config["mcpServers"][server_name] = self._translate_mcp_config(
                server_name, dict(config)
            )

        self.mcp_config_path = os.path.join(
            self.claude_config_dir, "mcp_servers.json")
        with open(self.mcp_config_path, "w") as f:
            json.dump(mcp_config, f, indent=2)

        logging.info(f"MCP server config written to {self.mcp_config_path}")

    def _install_mcp_servers_from_repo(self, mcp_servers: list):
        """Clones plugin marketplace repos that bundle MCP servers and enables them.

        Each entry: ``{action: "install_from_repo", url: "<git-url>[#tag]",
        plugin: "<optional-plugin-name>", config: {<userConfig values>}}``.
        Reuses the same marketplace clone/register machinery as `skills`
        install_from_repo: the repo's `.claude-plugin/marketplace.json` is
        registered as a `directory` source and the target plugin enabled via
        `enabledPlugins`, so Claude Code auto-starts the plugin's bundled MCP
        servers at startup.
        """
        setup_env = os.environ.copy()
        setup_env.update(self.env)

        marketplaces_dir = os.path.join(
            self.claude_config_dir, "plugins", "marketplaces")
        os.makedirs(marketplaces_dir, exist_ok=True)

        for mcp_config in mcp_servers:
            if not isinstance(mcp_config, dict):
                logging.warning(f"Unsupported MCP server config: {mcp_config}")
                continue
            action = mcp_config.get("action")
            url = mcp_config.get("url")
            if action != "install_from_repo" or not url:
                logging.warning(
                    f"Unsupported MCP server config: {mcp_config}. When "
                    "'mcp_servers' is a list, each entry must use "
                    "'action: install_from_repo' with 'url'.")
                continue
            marketplace_dir = self._clone_marketplace_repo(
                url, marketplaces_dir, setup_env)
            if marketplace_dir:
                self._register_marketplace_plugin(
                    marketplace_dir,
                    plugin_name=mcp_config.get("plugin"),
                    plugin_config=mcp_config.get("config"),
                    env=setup_env,
                )

    def _translate_mcp_config(self, server_name: str, config: dict) -> dict:
        """Translates a Gemini-style MCP server config into Claude Code format."""
        # Stdio server (command + args): pass through as-is
        if "command" in config:
            return config

        # HTTP/SSE server: translate Gemini-style `httpUrl` -> `url` + `type`
        if "httpUrl" in config and "url" not in config:
            config["url"] = config.pop("httpUrl")
        if "url" in config and "type" not in config:
            config["type"] = "http"

        # Translate `authProviderType: google_credentials` into Bearer header
        auth_provider = config.pop("authProviderType", None)
        # Gemini-style `oauth.scopes` is ignored by Claude Code; drop it
        config.pop("oauth", None)

        if auth_provider == "google_credentials":
            headers = config.get("headers", {}) or {}

            # Refresh the token on every MCP connection via `headersHelper`.
            # Claude Code runs this command each time it connects and lets its
            # JSON stdout override the static headers, so the bearer token
            # never expires mid-suite (baked static tokens live ~1h). If the
            # helper can't produce a token (gcloud/ADC unavailable in Claude
            # Code's env), it prints nothing and Claude Code falls back to the
            # static header below.
            config.setdefault(
                "headersHelper", self._google_credentials_headers_helper())

            # Baked static token: the initial value and the fallback for when
            # headersHelper can't run. headersHelper output takes precedence.
            if "Authorization" not in headers:
                token = self._fetch_gcloud_access_token()
                if token:
                    headers["Authorization"] = f"Bearer {token}"
                else:
                    logging.warning(
                        f"MCP server '{server_name}' requires google_credentials "
                        "but failed to fetch an access token via gcloud."
                    )
            config["headers"] = headers

        return config

    @staticmethod
    def _google_credentials_headers_helper() -> str:
        """Shell command Claude Code executes on each MCP connection to mint a
        fresh Google bearer token.

        Its stdout must be a JSON object of string headers, which Claude Code
        merges over the static ``headers`` -- giving a token that refreshes per
        connection instead of expiring ~1h after setup. Prefers ADC (required
        by Google API MCP endpoints), falls back to the gcloud user token, and
        prints nothing (non-zero exit) when neither is available so Claude Code
        keeps using the baked static header.
        """
        return (
            'tok="$(gcloud auth application-default print-access-token '
            '2>/dev/null || gcloud auth print-access-token 2>/dev/null)"; '
            '[ -n "$tok" ] && printf \'{"Authorization":"Bearer %s"}\' "$tok"'
        )

    def _fetch_gcloud_access_token(self) -> str:
        """Fetches a Google Cloud access token for MCP `google_credentials` auth.

        Prefers Application Default Credentials (``gcloud auth
        application-default print-access-token``) over the gcloud *user* token
        (``gcloud auth print-access-token``). Google API MCP endpoints such as
        Cloud SQL Managed (``sqladmin.googleapis.com/mcp``) reject the plain
        user token on tool calls ("Request had invalid authentication
        credentials") and only accept an ADC token.

        This matters because a rejected token makes the MCP *tool call* return
        ``401`` with a ``WWW-Authenticate: Bearer resource_metadata=...``
        challenge (``initialize``/``tools/list`` are unauthenticated, so the
        server connects fine and only the tool call fails). Claude Code always
        attaches an OAuth authProvider to HTTP MCP servers, so that 401 drives
        it into the OAuth flow -- and since these endpoints don't support OAuth
        dynamic client registration, the run fails with the confusing error
        "Incompatible auth server: does not support dynamic client
        registration". See docs/claude_code_agent_testing.md#troubleshooting.

        The token is fetched with the host environment (real HOME / gcloud
        config), NOT the sandboxed fake HOME -- gcloud must find the
        developer's / host's credentials. An explicit service-account or ADC
        file (Cloud Run / mounted SA key) is honored if one was configured, but
        HOME is never overridden (doing so points gcloud at the empty
        fake-home config and both commands fail).
        """
        token_env = os.environ.copy()
        adc = self.env.get("GOOGLE_APPLICATION_CREDENTIALS")
        if adc and os.path.exists(adc):
            token_env["GOOGLE_APPLICATION_CREDENTIALS"] = adc
        # ADC first (works for these endpoints); user token as a fallback for
        # any MCP server that happens to accept it.
        commands = [
            ["gcloud", "auth", "application-default", "print-access-token"],
            ["gcloud", "auth", "print-access-token"],
        ]
        for cmd in commands:
            try:
                result = subprocess.run(
                    cmd, check=True, capture_output=True, text=True,
                    env=token_env,
                )
                token = result.stdout.strip()
                if token:
                    logging.info(
                        f"MCP google_credentials token via `{' '.join(cmd)}` "
                        f"({len(token)} chars)"
                    )
                    return token
            except (subprocess.CalledProcessError, FileNotFoundError) as e:
                stderr = getattr(e, "stderr", "") or ""
                logging.warning(
                    f"`{' '.join(cmd)}` failed: {e}. {stderr.strip()}")
        logging.error(
            "Failed to fetch a Google Cloud access token via gcloud (tried ADC "
            "and user credentials). Run `gcloud auth application-default login`."
        )
        return ""

    def _setup_settings(self, settings_config: dict):
        """Writes Claude Code settings.json."""
        settings_path = os.path.join(self.claude_config_dir, "settings.json")

        current_settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f:
                    current_settings = json.load(f)
            except json.JSONDecodeError as e:
                logging.warning(
                    f"Invalid JSON in Claude Code settings at {settings_path}; "
                    f"proceeding with empty settings. Error: {e}"
                )

        current_settings.update(settings_config)

        with open(settings_path, "w") as f:
            json.dump(current_settings, f, indent=2)

        logging.info(f"Claude Code settings written to {settings_path}")

    def _setup_skills(self, skills: list):
        """Clones plugin marketplace repos and registers them in settings.json.

        Each skill config: {action: "install_from_repo", url: "<git-url>[#tag]"}.
        Each repo is expected to have `.claude-plugin/marketplace.json`. The
        marketplace is registered as a `directory` source (no network fetch at
        Claude Code startup) and the first declared plugin is enabled.
        """
        setup_env = os.environ.copy()
        setup_env.update(self.env)

        marketplaces_dir = os.path.join(
            self.claude_config_dir, "plugins", "marketplaces")
        os.makedirs(marketplaces_dir, exist_ok=True)

        for skill_config in skills:
            if not isinstance(skill_config, dict):
                logging.warning(f"Unsupported skill config: {skill_config}")
                continue
            action = skill_config.get("action")
            url = skill_config.get("url")
            if action != "install_from_repo" or not url:
                logging.warning(
                    f"Unsupported skill config: {skill_config}. "
                    "Only 'action: install_from_repo' with 'url' is supported.")
                continue
            marketplace_dir = self._clone_marketplace_repo(
                url, marketplaces_dir, setup_env)
            if marketplace_dir:
                self._register_marketplace_plugin(marketplace_dir, env=setup_env)

    def _setup_skills_from_dir(self, skills_dir_path: str):
        """Registers a local plugin marketplace directory in settings.json."""
        if not os.path.isdir(skills_dir_path):
            logging.warning(f"Skills directory not found: {skills_dir_path}")
            return
        self._register_marketplace_plugin(os.path.abspath(skills_dir_path))

    def _clone_marketplace_repo(
        self, url: str, marketplaces_dir: str, env: dict
    ) -> str | None:
        """Clones a plugin marketplace repo. Supports `<url>#<tag>` for version pinning."""
        clone_url, _, version_tag = url.partition("#")
        repo_name = re.sub(r"\.git$", "", clone_url.rstrip("/").split("/")[-1])
        clone_target = os.path.join(marketplaces_dir, repo_name)
        if os.path.exists(clone_target):
            shutil.rmtree(clone_target)

        cmd = ["git", "clone", "--depth", "1"]
        if version_tag:
            cmd.extend(["--branch", version_tag])
        cmd.extend([clone_url, clone_target])

        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, check=False,
                env=env, timeout=120,
            )
            if result.returncode != 0:
                logging.error(
                    f"Failed to clone repo '{url}': {result.stderr.strip()}")
                return None
            logging.info(f"Cloned plugin marketplace '{url}' to {clone_target}")
            return clone_target
        except subprocess.TimeoutExpired:
            logging.error(f"Cloning repo '{url}' timed out")
            return None

    def _register_marketplace_plugin(
        self, marketplace_dir: str, plugin_name: str | None = None,
        plugin_config: dict | None = None, env: dict | None = None,
    ):
        """Reads marketplace.json and updates settings.json so Claude Code
        auto-loads a marketplace plugin at startup.

        By default the marketplace's first plugin is enabled; pass
        ``plugin_name`` to enable a specific plugin instead (used by the
        MCP-from-repo flow when the MCP-bundling plugin isn't listed first).
        ``plugin_config`` supplies values for the plugin's ``userConfig``,
        written to ``pluginConfigs[<plugin-id>].options`` so ``${user_config.*}``
        placeholders in bundled MCP/hook configs resolve non-interactively.
        """
        marketplace_json_path = os.path.join(
            marketplace_dir, ".claude-plugin", "marketplace.json")
        if not os.path.exists(marketplace_json_path):
            logging.warning(
                f"No .claude-plugin/marketplace.json in {marketplace_dir}; "
                "cannot register as plugin marketplace.")
            return

        try:
            with open(marketplace_json_path, "r", encoding="utf-8") as f:
                marketplace_data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            logging.warning(f"Failed to read {marketplace_json_path}: {e}")
            return

        marketplace_name = marketplace_data.get("name")
        plugins = marketplace_data.get("plugins") or []
        if not marketplace_name or not plugins:
            logging.warning(
                f"marketplace.json missing 'name' or 'plugins': {marketplace_json_path}")
            return

        plugin_names = [
            (p.get("name") if isinstance(p, dict) else str(p)) for p in plugins
        ]
        if plugin_name:
            if plugin_name not in plugin_names:
                logging.warning(
                    f"Plugin '{plugin_name}' not found in {marketplace_json_path}; "
                    f"available: {plugin_names}")
                return
        else:
            plugin_name = plugin_names[0]
        if not plugin_name:
            logging.warning(f"First plugin entry has no name in {marketplace_json_path}")
            return

        settings_path = os.path.join(self.claude_config_dir, "settings.json")
        settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, "r") as f:
                    settings = json.load(f)
            except json.JSONDecodeError:
                settings = {}

        plugin_id = f"{plugin_name}@{marketplace_name}"

        settings.setdefault("extraKnownMarketplaces", {})[marketplace_name] = {
            "source": {
                "source": "directory",
                "path": os.path.abspath(marketplace_dir),
            }
        }
        settings.setdefault("enabledPlugins", {})[plugin_id] = True

        if plugin_config:
            settings.setdefault("pluginConfigs", {}).setdefault(
                plugin_id, {}).setdefault("options", {}).update(plugin_config)

        with open(settings_path, "w") as f:
            json.dump(settings, f, indent=2)
        logging.info(
            f"Registered plugin '{plugin_id}' "
            f"(directory source: {marketplace_dir})"
            + (f" with userConfig {list(plugin_config)}" if plugin_config else ""))

        self._install_plugin(plugin_id, env)

    def _install_plugin(self, plugin_id: str, env: dict | None = None):
        """Forces synchronous installation of a registered plugin."""
        if not env:
            env = os.environ.copy()
            env.update(self.env)

        cli = self.claude_code_version
        if cli.startswith("@") or "/" in cli:
            cli_base_cmd = ["npm", "exec", "--yes", cli, "--"]
        else:
            cli_base_cmd = [cli]

        # Run a dummy command to force initialization of projects/sessions
        # and plugin manager state. Ignore the failure (it will fail with
        # auth error if credentials are not ready, which is expected).
        init_command = cli_base_cmd + ["-p", "initialize_session"]
        logging.info(f"Initializing Claude Code state: {' '.join(init_command)}")
        self._execute_cli_command(init_command, env=env)

        # Now run the actual install command
        install_command = cli_base_cmd + ["plugins", "install", plugin_id]
        logging.info(f"Synchronously installing plugin: {' '.join(install_command)}")

        result = self._execute_cli_command(install_command, env=env)
        if result.returncode != 0:
            logging.error(f"Failed to install plugin '{plugin_id}': {result.stderr}")
        else:
            logging.info(f"Successfully installed plugin '{plugin_id}'")

    def generate_internal(self, cli_cmd, timeout_seconds=None):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.claude_code_version, str(cli_cmd))
        return self._run_claude_code(cli_cmd, timeout_seconds=timeout_seconds)

    def _execute_cli_command(
        self, command: list[str], env: dict[str, str] | None = None,
        cwd: str | None = None, timeout_seconds: float | int | None = None,
    ) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, env=env,
                cwd=cwd if cwd else self.fake_home, stdin=subprocess.DEVNULL,
                timeout=timeout_seconds,
            )
            return result
        except subprocess.TimeoutExpired as e:
            stdout_str = e.stdout if isinstance(e.stdout, str) else (e.stdout.decode() if e.stdout else "")
            stderr_str = f"TimeoutError: Command timed out after {timeout_seconds} seconds"
            if e.stderr:
                err_text = e.stderr if isinstance(e.stderr, str) else e.stderr.decode()
                stderr_str = f"{stderr_str}\n{err_text}"
            return subprocess.CompletedProcess(
                command, 124, stdout_str, stderr_str
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                command, 127, "", f"Error: Command not found: {command[0]}"
            )
        except Exception as e:
            return subprocess.CompletedProcess(
                command, 1, "", f"An unexpected error occurred: {e}"
            )

    def _run_claude_code(self, cli_cmd: CLICommand, timeout_seconds=None):
        env = os.environ.copy()
        env.update(self.env)
        env.update(cli_cmd.env)

        # If the version looks like an npm package spec (contains "/" or starts
        # with "@"), use `npm exec` to pin that version (like Gemini CLI does).
        # Otherwise, invoke the binary directly (e.g. "claude").
        cli = cli_cmd.cli
        if cli.startswith("@") or "/" in cli:
            command = ["npm", "exec", "--yes", cli, "--"]
        else:
            command = [cli]

        # -p "prompt" for non-interactive single-prompt mode
        command.extend(["-p", cli_cmd.prompt])

        # Auto-accept all tool uses (like --yolo in Gemini CLI)
        command.append("--dangerously-skip-permissions")

        # Output format (stream-json requires --verbose with --print)
        command.extend(["--output-format", "stream-json", "--verbose"])

        # Model override
        model = self.model
        if model:
            command.extend(["--model", model])

        # MCP server config
        if hasattr(self, "mcp_config_path") and os.path.exists(self.mcp_config_path):
            command.extend(["--mcp-config", self.mcp_config_path])

        # Resume session: `--resume <session_id>` takes the UUID as its value.
        # `--fork-session` creates a new session ID from the resumed history,
        # which is required when scenarios run concurrently (they share the
        # same sandboxed ~/.claude session store and otherwise conflict).
        if cli_cmd.resume and cli_cmd.session_id:
            command.extend(["--resume", cli_cmd.session_id, "--fork-session"])

        # Allowed tools
        allowed_tools = cli_cmd.allowedTools or self.allowed_tools
        if allowed_tools:
            for tool in allowed_tools:
                command.extend(["--allowedTools", tool])

        # Claude Code refuses --dangerously-skip-permissions when running as
        # root.  Wrap with `su` to drop privileges to a non-root user.
        # Recursively chown the fake_home so claudeuser can write to it
        # (covers .claude dir, gcloud creds, MCP config copied during init).

        logging.info(f"Running Claude Code CLI: {' '.join(command)}")

        result = self._execute_cli_command(command, env=env, cwd=cli_cmd.cwd, timeout_seconds=timeout_seconds)
        if result.stdout:
            result.stdout = self._parse_stream_json(result.stdout)

        return result

    @staticmethod
    def _stringify_tool_result(content) -> str:
        """Normalizes a tool_result `content` (string or list of content
        blocks) into a plain-text response string."""
        if isinstance(content, str):
            return content
        if isinstance(content, list):
            parts = []
            for block in content:
                if isinstance(block, dict):
                    parts.append(block.get("text", "") or "")
                else:
                    parts.append(str(block))
            return "".join(parts)
        return "" if content is None else str(content)

    def _record_tool_result(self, tool_calls_dict, tool_id, is_error, content):
        """Records a tool result's status/response against its originating
        tool_use id (no-op if the id isn't a known tool call)."""
        if not tool_id or tool_id not in tool_calls_dict:
            return
        tool_calls_dict[tool_id]["status"] = "error" if is_error else "success"
        tool_calls_dict[tool_id]["response"] = self._stringify_tool_result(
            content)

    def _parse_stream_json(self, stream_output: str) -> str:
        """Parses Claude Code stream-json output into a normalized format
        compatible with the eval pipeline."""

        from collections import OrderedDict

        final_obj = {"session_id": "", "response": "", "stats": {}, "tool_calls": []}
        tool_calls_dict = OrderedDict()
        # Fall back to the configured model if the stream's `system` init
        # event doesn't include one (e.g., truncated output).
        model_name = self.model or "unknown"

        for line in stream_output.split("\n"):
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
                event_type = event.get("type")

                if event_type == "system":
                    final_obj["session_id"] = event.get("session_id", "")
                    if "model" in event:
                        model_name = event["model"]

                elif event_type == "assistant":
                    # Assistant message with content blocks
                    message = event.get("message", {})
                    content_blocks = message.get("content", [])
                    for block in content_blocks:
                        if not isinstance(block, dict):
                            continue
                        block_type = block.get("type")
                        if block_type == "text":
                            final_obj["response"] += block.get("text", "")
                        elif block_type == "tool_use":
                            tool_id = block.get("id", "")
                            # Claude Code emits MCP tools as
                            # ``mcp__<server>__<tool>``. Normalize to the
                            # canonical ``<server>__<tool>`` form so the
                            # trajectory matcher can compare across
                            # harnesses without per-generator logic.
                            raw_name = block.get("name", "unknown")
                            tname = canonicalize_claude_tool_name(raw_name)
                            if tool_id:
                                tool_calls_dict[tool_id] = {
                                    "tool_id": tool_id,
                                    "tool_name": tname,
                                    "parameters": block.get("input", {}),
                                    "status": None,
                                    "response": None,
                                }

                elif event_type == "tool_result":
                    # Some streams emit a top-level tool_result event.
                    self._record_tool_result(
                        tool_calls_dict,
                        event.get("tool_use_id") or event.get("id", ""),
                        event.get("is_error", False),
                        event.get("content", ""),
                    )

                elif event_type == "user":
                    # Claude Code delivers tool results inside user-role
                    # messages as `tool_result` content blocks, not as
                    # top-level `tool_result` events. Without this, every MCP
                    # tool call stays status=None (scored as a failure) even
                    # when it succeeded.
                    message = event.get("message", {})
                    for block in message.get("content", []):
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_result":
                            continue
                        self._record_tool_result(
                            tool_calls_dict,
                            block.get("tool_use_id") or block.get("id", ""),
                            block.get("is_error", False),
                            block.get("content", ""),
                        )

                elif event_type == "result":
                    if "session_id" in event:
                        final_obj["session_id"] = event["session_id"]

                    # Use result.usage for authoritative token counts
                    usage = event.get("usage", {})
                    total_input_tokens = usage.get("input_tokens", 0)
                    total_output_tokens = usage.get("output_tokens", 0)
                    total_cache_read = usage.get("cache_read_input_tokens", 0)
                    total_cache_creation = usage.get(
                        "cache_creation_input_tokens", 0)
                    total_tokens = total_input_tokens + total_output_tokens

                    duration_ms = event.get("duration_ms", 0)
                    cost_usd = event.get("total_cost_usd", 0)

                    # Use modelUsage if available for per-model breakdown
                    model_usage = event.get("modelUsage", {})

                    models = {}
                    if model_usage:
                        for m_name, m_stats in model_usage.items():
                            m_input = m_stats.get("inputTokens", 0)
                            m_output = m_stats.get("outputTokens", 0)
                            m_cached = m_stats.get("cacheReadInputTokens", 0)
                            m_cache_creation = m_stats.get(
                                "cacheCreationInputTokens", 0)
                            models[m_name] = {
                                "api": {
                                    "totalRequests": 1,
                                    "totalErrors": 0,
                                    "totalLatencyMs": duration_ms,
                                },
                                "tokens": {
                                    "input": m_input,
                                    "prompt": m_input,
                                    "candidates": m_output,
                                    "total": m_input + m_output,
                                    "cached": m_cached,
                                    "cache_creation": m_cache_creation,
                                    "thoughts": 0,
                                    "tool": 0,
                                },
                                "cost_usd": m_stats.get("costUSD", 0),
                                "roles": {
                                    "main": {
                                        "totalRequests": 1,
                                        "totalErrors": 0,
                                        "totalLatencyMs": duration_ms,
                                        "tokens": {
                                            "input": m_input,
                                            "prompt": m_input,
                                            "candidates": m_output,
                                            "total": m_input + m_output,
                                            "cached": m_cached,
                                            "thoughts": 0,
                                            "tool": 0,
                                        },
                                    }
                                },
                            }
                    else:
                        models[model_name] = {
                            "api": {
                                "totalRequests": 1,
                                "totalErrors": 0,
                                "totalLatencyMs": duration_ms,
                            },
                            "tokens": {
                                "input": total_input_tokens,
                                "prompt": total_input_tokens,
                                "candidates": total_output_tokens,
                                "total": total_tokens,
                                "cached": total_cache_read,
                                "cache_creation": total_cache_creation,
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
                                        "input": total_input_tokens,
                                        "prompt": total_input_tokens,
                                        "candidates": total_output_tokens,
                                        "total": total_tokens,
                                        "cached": total_cache_read,
                                        "thoughts": 0,
                                        "tool": 0,
                                    },
                                }
                            },
                        }
                    final_obj["stats"]["models"] = models

                    # Build tool stats
                    tools_stats = {
                        "totalCalls": len(tool_calls_dict),
                        "totalSuccess": sum(
                            1
                            for tc in tool_calls_dict.values()
                            if tc.get("status") == "success"
                        ),
                        "totalFail": sum(
                            1
                            for tc in tool_calls_dict.values()
                            if tc.get("status") != "success"
                        ),
                        "totalDurationMs": 0,
                        "decisions": {
                            "accept": len(tool_calls_dict),
                            "reject": 0,
                            "modify": 0,
                            "auto_accept": len(tool_calls_dict),
                        },
                        "byName": {},
                    }

                    for tc in tool_calls_dict.values():
                        tname = tc.get("tool_name", "unknown")
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
                        tstat["parameters"].append(tc.get("parameters", {}))
                        tstat["decisions"]["accept"] += 1
                        tstat["decisions"]["auto_accept"] += 1

                        if tc.get("status") == "success":
                            tstat["success"] += 1
                        elif tc.get("status") == "error":
                            tstat["fail"] += 1

                    final_obj["stats"]["tools"] = tools_stats

                    # Store the final text response from result if not yet captured
                    if not final_obj["response"] and event.get("result"):
                        final_obj["response"] = event["result"]

            except Exception as e:
                logging.debug(f"Failed to parse stream JSON line: {e}")

        final_obj["tool_calls"] = list(tool_calls_dict.values())
        return json.dumps(final_obj, indent=2)

    @property
    def version(self) -> str:
        return self.claude_code_version

    def parse_response(self, stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            logging.error(f"Failed to parse JSON response: {stdout[:100]}...")
            return {}

    def extract_tools(self, stdout: str) -> list[str]:
        """Extracts the list of tools used from the CLI output.

        Returns every tool the harness recorded -- MCP calls in canonical
        ``<server>__<tool>`` form alongside native tools (``Read``,
        ``Bash``, ``Edit``, ``ToolSearch``, ...). The trajectory scorer
        is responsible for filtering native/harness-internal tools when
        ``filter_native_tools`` is enabled.
        """
        output_json = self.parse_response(stdout)
        if (
            "stats" in output_json
            and "tools" in output_json["stats"]
            and "byName" in output_json["stats"]["tools"]
        ):
            return list(output_json["stats"]["tools"]["byName"].keys())
        return []

    def _get_installed_skills(self) -> set[str]:
        """Returns the set of skill directory names installed via plugin marketplaces."""
        installed = set()
        marketplaces_root = os.path.join(
            self.claude_config_dir, "plugins", "marketplaces")
        if os.path.isdir(marketplaces_root):
            for marketplace in os.listdir(marketplaces_root):
                self._collect_skills(
                    os.path.join(marketplaces_root, marketplace, "skills"),
                    installed,
                )
        skills_dir_path = (self.setup_config or {}).get("skills_dir")
        if skills_dir_path:
            self._collect_skills(
                os.path.join(skills_dir_path, "skills"), installed)
        return installed

    @staticmethod
    def _collect_skills(skills_root: str, into: set):
        if not os.path.isdir(skills_root):
            return
        for entry in os.listdir(skills_root):
            if os.path.exists(os.path.join(skills_root, entry, "SKILL.md")):
                into.add(entry)

    def _extract_script_names(self, by_name: dict) -> list[str]:
        """Extracts script names (e.g. list_instances.js) from Bash tool invocations."""
        scripts = []
        for tool_name, tstat in by_name.items():
            if tool_name.lower() != "bash":
                continue
            for params in tstat.get("parameters", []) or []:
                command = params.get("command", "") if isinstance(params, dict) else ""
                match = re.search(r'/scripts/([a-z_-]+\.js)', command)
                if match and match.group(1) not in scripts:
                    scripts.append(match.group(1))
        return scripts

    def extract_skills(self, stdout: str) -> list[str]:
        """Extracts the names of skills activated during the run.

        Returns only true skill names (those with a SKILL.md), not the
        bash scripts a skill may invoke internally. Use `extract_skill_scripts`
        for trajectory-style script-name extraction.
        """
        output_json = self.parse_response(stdout)
        try:
            by_name = output_json["stats"]["tools"]["byName"]
        except (KeyError, TypeError):
            return []

        installed_skills = self._get_installed_skills()
        items = []

        # Pattern 1: a skill's tool surfaces directly under its own name
        for tool_name in by_name:
            if tool_name in installed_skills and tool_name not in items:
                items.append(tool_name)

        # Pattern 2: the built-in `Skill` tool with the skill name as a parameter
        skill_tool = by_name.get("Skill", {})
        for params in skill_tool.get("parameters", []):
            name = params.get("skill") or params.get("name") or params.get("skill_name")
            if name and name not in items:
                items.append(name)

        return items

    def extract_skill_scripts(self, stdout: str) -> list[str]:
        """Extracts skill-script names (e.g. list_instances.js) from Bash invocations."""
        output_json = self.parse_response(stdout)
        try:
            by_name = output_json["stats"]["tools"]["byName"]
        except (KeyError, TypeError):
            return []
        return self._extract_script_names(by_name)

    def safe_generate(
        self, cli_cmd: CLICommand, timeout_seconds: Optional[float] = None
    ) -> subprocess.CompletedProcess:
        result = self.generate_internal(cli_cmd, timeout_seconds=timeout_seconds)
        if isinstance(result, str):
            return subprocess.CompletedProcess(args=[], returncode=0, stdout=result)

        if not result.stdout and result.returncode != 0:
            result.stderr += "\nError: Generator returned empty response."
        return result

    def create_command(
        self, cli: str, prompt: str, env: dict = None, resume: bool = False,
        session_id: str = None, cwd: str = None,
    ) -> CLICommand:
        merged_env = self.env.copy()
        if env:
            merged_env.update(env)
        return CLICommand(
            cli=cli, prompt=prompt, env=merged_env,
            resume=resume, session_id=session_id, cwd=cwd,
        )
