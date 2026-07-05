from .agent_cli import AgentCliGenerator
from .tool_naming import canonicalize_agy_tool_name, parse_agy_mcp_tool_call
import collections
import subprocess
import os
import json
import logging
import re
import shutil
import sys
import dateutil.parser
from util.context import rpc_id_var

# Bare command name. agy's installer exposes no version pinning and the binary
# self-updates in the background, so there is nothing to configure. This is the
# reported agent_version label only -- the binary actually launched is the
# per-session install at self.agy_bin (see _ensure_agy_installed).
AGY_CLI = "agy"

# Upstream one-line installer. Honors --dir (and $HOME) for the install
# location and skips the download when the binary already exists at the target.
AGY_INSTALL_URL = "https://antigravity.google/cli/install.sh"


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, cwd=None):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.cwd = cwd


class AgyCliGenerator(AgentCliGenerator):
    """Generator that queries via the Antigravity CLI (``agy``).

    Surface targeted here is what the v1.0.5 binary actually exposes:
    ``agy -p <prompt> --dangerously-skip-permissions [--model <label>]
    [--continue]``. The on-disk layout lives under
    ``~/.gemini/antigravity-cli/`` (the binary calls this ``appDataDir``).
    Skills are delivered via plugins (see _setup_skills). There is no
    ``--output-format`` flag and no stdout stream protocol; structured
    tool-call data is read out of the per-conversation JSONL transcript at
    ``<appDataDir>/brain/<uuid>/.system_generated/logs/transcript.jsonl``.
    """

    APP_DATA_SUBPATH = os.path.join(".gemini", "antigravity-cli")

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "agy_cli"

        # Parity with gemini_cli_version/codex_cli_version/claude_code_version:
        # the evaluator reads this as agent_version. Fixed to the bare command
        # name (see AGY_CLI) and intentionally not config-overridable.
        self.agy_cli_version = AGY_CLI

        self.env = querygenerator_config.get("env") or {}

        # Top-level `model` key, applied per-invocation via agy's `--model`
        # flag (None -> flag omitted). See _base_agy_command for the value
        # format and resolution semantics.
        self.model = querygenerator_config.get("model")

        # Order is load-bearing: paths/dirs must exist before the binary
        # installs and settings/auth write into them, and self.env must carry
        # HOME before the installer stages files (and auth resolves ADC) into
        # the sandbox. Keep these calls in sequence.
        self._init_paths(querygenerator_config)
        self.env["HOME"] = self.fake_home
        self._ensure_agy_installed()
        self._initialize_settings_file()
        self._setup_auth()

        self.setup_config = querygenerator_config.get("setup", {})
        if self.setup_config:
            self._setup_tools()

    def _init_paths(self, querygenerator_config):
        """Resolves the sandbox ``HOME`` and all derived agy paths, and
        creates the directories agy will read/write."""
        self.real_home = os.environ.get("HOME", os.path.expanduser("~"))

        if sys.argv[0].endswith("eval_server.py"):
            session_id = querygenerator_config.get("session_id")
            if not session_id:
                ctx_id = rpc_id_var.get()
                session_id = ctx_id if ctx_id != "default" else "default"
            self.fake_home = os.path.join(
                "/tmp_sessions", session_id, "fake_home"
            )
        else:
            self.fake_home = os.path.abspath(
                os.path.join(".venv", "fake_home_agy")
            )

        # The agy binary is installed per-session under fake_home (not on the
        # host PATH or in the Docker image) -- see _ensure_agy_installed. The
        # installer's default target is $HOME/.local/bin, which we pin
        # explicitly via --dir so it does not depend on HOME resolution.
        self.bin_dir = os.path.join(self.fake_home, ".local", "bin")
        self.agy_bin = os.path.join(self.bin_dir, "agy")

        self.app_data_dir = os.path.join(self.fake_home, self.APP_DATA_SUBPATH)
        self.settings_path = os.path.join(self.app_data_dir, "settings.json")
        self.config_dir = os.path.join(self.fake_home, ".gemini", "config")
        self.mcp_config_path = os.path.join(self.config_dir, "mcp_config.json")
        # agy records installed plugins (which carry the skills) here.
        self.plugin_manifest_path = os.path.join(
            self.config_dir, "import_manifest.json"
        )

        os.makedirs(self.fake_home, exist_ok=True)
        os.makedirs(self.bin_dir, exist_ok=True)
        os.makedirs(self.app_data_dir, exist_ok=True)
        os.makedirs(os.path.dirname(self.settings_path), exist_ok=True)
        os.makedirs(self.config_dir, exist_ok=True)

    def _ensure_agy_installed(self):
        """Installs the ``agy`` binary into this session's sandbox if absent.

        The binary lives under the per-session ``fake_home``
        (``self.agy_bin``) rather than on the host PATH or baked into the
        Docker image. Per-session keeps concurrent evals isolated: no install
        race between sessions, and no shared binary that agy's background
        self-update could swap mid-run -- which would otherwise skew the agent
        version across a single batch.

        The upstream installer skips the download when the binary already
        exists, and we short-circuit on the same check, so a generator
        re-constructed within a live session is a cheap stat.
        """
        if os.path.exists(self.agy_bin) and os.access(self.agy_bin, os.X_OK):
            logging.info(
                "agy binary already present at %s; skipping install.",
                self.agy_bin,
            )
            return

        env = self._merged_env()
        staging_dir = os.path.join(self.fake_home, ".cache", "agy_install")
        os.makedirs(staging_dir, exist_ok=True)
        script_path = os.path.join(staging_dir, "install.sh")

        # Two argv-list steps (no shell): fetch the installer to a file, then
        # run it with an explicit --dir. The canonical ``curl | bash`` pipe
        # would need a shell, which would interpolate the session-derived
        # install dir into a command string; argv lists avoid that entirely.
        steps = (
            (["curl", "-fsSL", "-o", script_path, AGY_INSTALL_URL],
             "download agy installer"),
            (["bash", script_path, "--dir", self.bin_dir],
             "install agy binary"),
        )
        for cmd, what in steps:
            try:
                result = subprocess.run(
                    cmd, env=env, stdin=subprocess.DEVNULL,
                    capture_output=True, text=True,
                    timeout=300, check=False,
                )
            except (FileNotFoundError, subprocess.TimeoutExpired) as e:
                raise RuntimeError(f"Failed to {what}: {e}") from e
            if result.returncode != 0:
                raise RuntimeError(
                    f"Failed to {what} (rc={result.returncode}): "
                    f"{(result.stderr or result.stdout or '').strip()}"
                )

        if not (os.path.exists(self.agy_bin)
                and os.access(self.agy_bin, os.X_OK)):
            raise RuntimeError(
                f"agy installer ran but produced no executable at "
                f"{self.agy_bin}."
            )
        logging.info("Installed agy into session sandbox at %s.", self.agy_bin)

    def _setup_auth(self):
        """Seeds agy's OAuth state into the sandbox and wires up gcloud ADC
        so the sandboxed CLI authenticates without an interactive login."""
        self._mirror_agy_auth_state()

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
            fake_adc_path = os.path.join(
                fake_gcloud_dir, "application_default_credentials.json"
            )
            if os.path.abspath(adc_path) != os.path.abspath(fake_adc_path):
                shutil.copy2(adc_path, fake_adc_path)

        if "CLOUDSDK_CONFIG" not in self.env:
            self.env["CLOUDSDK_CONFIG"] = os.path.join(
                self.real_home, ".config", "gcloud"
            )

    def _mirror_agy_auth_state(self):
        """Mirrors agy's OAuth token + installation id from the host's real
        appDataDir into the sandboxed appDataDir so the sandboxed CLI does not
        re-prompt for an interactive login.

        agy is OAuth-only (no env-var API key, no ADC), and the harness
        overrides ``HOME``, so without this the sandbox looks like a
        brand-new install and ``agy -p`` blocks on the device-code URL.

        Auth comes from the host's real appDataDir at
        ``~/.gemini/antigravity-cli/`` -- run ``agy`` once interactively to
        seed it; this then refreshes the copy on every run. The token is
        load-bearing (required=True); a missing installation_id is non-fatal
        for agy (required=False).
        """
        real_app_data = os.path.join(self.real_home, self.APP_DATA_SUBPATH)

        auth_files = (
            ("antigravity-oauth-token", True),
            ("installation_id", False),
        )

        for fname, required in auth_files:
            dst = os.path.join(self.app_data_dir, fname)
            src = os.path.join(real_app_data, fname)
            if not os.path.exists(src):
                if required:
                    logging.warning(
                        "agy OAuth token not found at %s -- run `agy` "
                        "interactively once to authenticate.",
                        src,
                    )
                continue
            try:
                shutil.copy2(src, dst)
                os.chmod(dst, 0o600)
            except OSError as e:
                logging.warning(
                    "Failed to mirror agy auth file %s -> %s: %s",
                    src, dst, e,
                )

    def _initialize_settings_file(self):
        """Writes the ``gcp.project``/``gcp.location`` block into agy's
        ``settings.json``.

        This block is load-bearing: agy resolves the project for its Vertex
        model backend from ``settings.json`` -> ``gcp.project``, **not** from
        the ``GOOGLE_CLOUD_PROJECT`` env var (verified empirically -- with the
        block removed, every ``agy -p`` turn returns an empty response and
        makes no tool calls, even though ``GOOGLE_CLOUD_PROJECT`` is exported
        and the MCP server still attaches). This is why agy is the only
        harness that writes a gcp block; the others pass the project purely
        through the environment.

        The model is intentionally *not* written here -- it is selected
        per-invocation via the ``--model`` flag (see _base_agy_command).
        """
        current_settings = {}
        if os.path.exists(self.settings_path):
            try:
                with open(self.settings_path, "r") as f:
                    current_settings = json.load(f)
            except json.JSONDecodeError:
                logging.warning(
                    "Invalid JSON in agy settings at %s; using defaults.",
                    self.settings_path,
                )

        gcp_config = current_settings.setdefault("gcp", {})

        # Resolve project/location, preferring (in order): the env / config,
        # then any values the sandbox settings.json already carries from a
        # previous run, then the host's real settings.json. The model is not
        # resolved here -- it is passed per-invocation via the ``--model`` flag.
        project = (
            self.env.get("GOOGLE_CLOUD_PROJECT") or gcp_config.get("project")
        )
        location = (
            self.env.get("GOOGLE_CLOUD_LOCATION") or gcp_config.get("location")
        )

        # Only consult the host's real settings.json for whatever the env and
        # sandbox file did not already supply. When the sandbox already covers
        # everything we skip the read entirely -- both to avoid the extra I/O
        # and to avoid noise from an empty/absent real file, which is a normal
        # state for sandboxed and CI runs.
        if project and location:
            logging.info(
                "agy settings: project/location satisfied by env and "
                "sandbox %s; skipping real settings.json read.",
                self.settings_path,
            )
        else:
            real_gcp = self._read_real_settings().get("gcp", {})
            project = project or real_gcp.get("project")
            location = location or real_gcp.get("location")

        location = location or "global"

        if project:
            gcp_config["project"] = project
        gcp_config["location"] = location

        logging.info(
            "agy settings resolved: project=%s location=%s",
            project, location,
        )

        with open(self.settings_path, "w") as f:
            json.dump(current_settings, f, indent=2)

    def _read_real_settings(self) -> dict:
        """Reads the host's real ``settings.json`` as a fallback source for
        project/location. Returns ``{}`` when the file is absent or
        empty -- both are normal states (e.g. sandboxed/CI runs, or a fresh
        agy install) and not worth a warning. Only genuinely malformed
        (non-empty, non-JSON) content is warned about.
        """
        path = os.path.join(
            self.real_home, self.APP_DATA_SUBPATH, "settings.json"
        )
        if not os.path.exists(path):
            logging.info(
                "agy real settings.json not present at %s; using defaults.",
                path,
            )
            return {}
        try:
            with open(path, "r") as f:
                raw = f.read().strip()
        except OSError as e:
            logging.warning(
                "Failed to read real settings.json %s: %s", path, e
            )
            return {}
        if not raw:
            logging.info(
                "agy real settings.json at %s is empty; using defaults.", path,
            )
            return {}
        try:
            return json.loads(raw)
        except json.JSONDecodeError as e:
            logging.warning(
                "Ignoring malformed real settings.json at %s: %s", path, e,
            )
            return {}

    def _setup_tools(self):
        """Performs initial setup for agy CLI."""
        mcp_servers_config = self.setup_config.get("mcp_servers", {})
        self._setup_mcp_servers(mcp_servers_config)
        if "fake_mcp_servers" in self.setup_config:
            self._setup_mcp_servers(self.setup_config["fake_mcp_servers"])

        skills_config = self.setup_config.get("skills", [])
        self._setup_skills(skills_config)

        # Probe agy once now -- before any scenarios run -- to detect
        # account-eligibility / MCP-load failures and fail fast with a
        # clear error instead of silently degrading to gcloud shell-outs.
        configured_servers = list(mcp_servers_config or {}) + list(
            self.setup_config.get("fake_mcp_servers", {}) or {}
        )
        if configured_servers:
            self._verify_mcp_runtime(configured_servers)

    # Transcript step type agy's runtime writes for an executed MCP call.
    # A genuine ``call_mcp_tool`` wrapper invocation is always recorded as a
    # dedicated ``MCP_TOOL`` result step (native tools get VIEW_FILE,
    # RUN_COMMAND, etc.), so this is the signal that the wrapper actually ran.
    _AGY_MCP_RESULT_TYPE = "MCP_TOOL"

    # Transcript step types / fields used while parsing a turn.
    _STEP_USER_INPUT = "USER_INPUT"
    _STEP_PLANNER_RESPONSE = "PLANNER_RESPONSE"
    _STEP_STATUS_DONE = "DONE"
    # ``source`` value agy stamps on every model-emitted step (tool results
    # and planner responses alike).
    _SOURCE_MODEL = "MODEL"
    # MODEL steps that are not themselves results (they carry no tool output).
    _NON_RESULT_MODEL_TYPES = (
        None, "PLANNER_RESPONSE", "CONVERSATION_HISTORY", "GENERIC",
    )

    # cli.log line agy emits once it has resolved the model for a run, e.g.
    #   model_config_manager.go:157] Propagating selected model override to
    #   backend: label="Gemini 3.5 Flash (High)"
    # This is the only on-disk record of the *resolved* model: it appears
    # whether the model came from --model, settings.json, or agy's own default
    # (the transcript only records a model when the user explicitly changes it).
    # Used to label the stats bucket when no model is configured -- see
    # _detect_model_from_log.
    _MODEL_LABEL_RE = re.compile(
        r'Propagating selected model override to backend: label="([^"]+)"'
    )

    # Bucket label when the resolved model can't be determined from any source.
    _DEFAULT_MODEL_LABEL = "agy"

    # Transcripts carry no token counts, so every token bucket is zero.
    _ZERO_TOKENS = {
        "input": 0, "prompt": 0, "candidates": 0,
        "total": 0, "cached": 0, "thoughts": 0, "tool": 0,
    }

    # Fatal log-line markers that mean MCP will not work in this run.
    _MCP_FATAL_MARKERS = (
        "Account ineligible",
        "failed to read mcp_config",
        "invalid mcp_config",
        "failed to start mcp instance",
        "failed to parse mcp_config_json",
    )

    def _verify_mcp_runtime(self, configured_servers: list):
        """Spawns a short-lived ``agy -p`` probe and confirms each
        configured MCP server actually attached and discovered tools.

        Validates attachment by checking the disk cache
        (``<appDataDir>/mcp/<server>/<tool>.json``), which agy populates
        during initialization. This prevents silent failures where agy
        accepts an invalid config but exposes zero tools without logging a
        fatal error.

        Prior to the probe, we clear the schema directory to ensure we don't
        read a stale cache. Fatal logs are still scanned to enrich error
        messages, and the probe is bounded by a timeout.
        """
        mcp_schema_root = os.path.join(self.app_data_dir, "mcp")
        for server in configured_servers:
            stale = os.path.join(mcp_schema_root, server)
            if os.path.isdir(stale):
                shutil.rmtree(stale, ignore_errors=True)

        log_dir = os.path.join(self.app_data_dir, "log")
        before = set(os.listdir(log_dir)) if os.path.isdir(log_dir) else set()

        env = self._merged_env()
        cmd = self._base_agy_command(self.agy_bin, "ping", model=self.model)
        try:
            subprocess.run(
                cmd, env=env, cwd=self.fake_home,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=120, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(
                f"agy MCP verification probe failed to run: {e}. "
                f"Configured MCP servers: {configured_servers}.\n"
                f"STDOUT:\n{getattr(e, 'stdout', '')}\n"
                f"STDERR:\n{getattr(e, 'stderr', '')}"
            ) from e

        # Collect fatal log markers (diagnostic context for any failure).
        after = set(os.listdir(log_dir)) if os.path.isdir(log_dir) else set()
        new_logs = sorted(after - before)
        marker_hits = []
        if new_logs:
            probe_log = os.path.join(log_dir, new_logs[-1])
            try:
                with open(probe_log, "r") as f:
                    for line in f:
                        if any(m in line for m in self._MCP_FATAL_MARKERS):
                            marker_hits.append(line.rstrip())
            except OSError as e:
                logging.warning(
                    "agy MCP probe log %s unreadable: %s", probe_log, e,
                )

        # Authoritative check: each server must have discovered >=1 tool.
        failed = []
        loaded = {}
        for server in configured_servers:
            server_dir = os.path.join(mcp_schema_root, server)
            tools = []
            if os.path.isdir(server_dir):
                for f in sorted(os.listdir(server_dir)):
                    if not f.endswith(".json"):
                        continue
                    path = os.path.join(server_dir, f)
                    if self._is_tool_schema_file(path):
                        tools.append(f[:-len(".json")])
                    else:
                        logging.warning(
                            "agy MCP schema cache file %s is not a valid "
                            "tool schema; not counting it as a discovered "
                            "tool.", path,
                        )
            if tools:
                loaded[server] = sorted(tools)
            else:
                failed.append(server)

        if failed:
            msg = (
                f"agy MCP server(s) {failed} attached no tools "
                f"(no schemas under {mcp_schema_root}/<server>/). The "
                "server likely failed to load -- check the URL field "
                "(use 'serverUrl' or 'url'; a gemini-style 'httpUrl' is "
                "auto-translated), auth, and "
                "reachability. agy degrades silently to shell-outs when "
                "MCP tools are missing."
            )
            if marker_hits:
                msg += "\nProbe log fatal markers:\n" + "\n".join(
                    f"  {h}" for h in marker_hits
                )
            raise RuntimeError(msg)

        for server, tools in loaded.items():
            logging.info(
                "agy MCP server '%s' attached %d tools: %s",
                server, len(tools), tools,
            )

    @staticmethod
    def _is_tool_schema_file(path: str) -> bool:
        """Return True iff ``path`` holds a real agy tool-schema cache entry.

        agy writes one JSON file per discovered tool at attach time, each a
        JSON object carrying at least the tool's ``name``. We validate that
        shape rather than trusting any ``*.json`` present so a stray sidecar
        file or leftover junk in ``<appDataDir>/mcp/<server>/`` can't be
        miscounted as a discovered tool -- which would let a silent attach
        failure pass verification.
        """
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (OSError, json.JSONDecodeError):
            return False
        return isinstance(data, dict) and bool(data.get("name"))

    # A target is a git URL (to be cloned) rather than a local path when it
    # carries a remote scheme or ends in ``.git``.
    _GIT_URL_PATTERN = re.compile(r"^(https?|git|ssh)://|^git@|\.git(#.*)?$")

    def _setup_skills(self, skills: list):
        """Installs skill-bearing plugins via ``agy plugin install``.

        Skills are delivered as plugins -- there is no ``agy skills``
        subcommand -- so the harness shells out to ``agy plugin install``
        for each entry. Two input shapes are supported, matching codex_cli
        and claude_code:

        * ``"<target>"`` -- a local plugin directory or a git URL (cloned
          first; ``agy plugin install`` requires a directory target).
        * ``{"action": "install_from_repo", "url"|"path": "..."}`` -- same,
          via an explicit dict.

        A ``plugin@marketplace`` spec is not a reliable target; use a git
        URL or local directory. See docs/agy_cli_agent_testing.md.
        """
        if not skills:
            return

        clone_workdir = os.path.join(self.app_data_dir, ".skill_clones")
        os.makedirs(clone_workdir, exist_ok=True)

        setup_env = self._merged_env()

        installed_any = False
        for skill_config in skills:
            target = self._resolve_skill_target(skill_config)
            if not target:
                continue
            if self._GIT_URL_PATTERN.search(target):
                target = self._clone_skill_repo(
                    target, clone_workdir, setup_env
                )
                if not target:
                    continue
            if self._install_agy_plugin(target, setup_env):
                installed_any = True

        if installed_any:
            self._log_installed_plugins()

    def _resolve_skill_target(self, skill_config) -> str:
        """Maps a skills-config entry to an ``agy plugin install`` target.

        Returns an install target (local dir or git URL) or an empty
        string when the entry is unusable.
        """
        if isinstance(skill_config, str):
            return skill_config
        if isinstance(skill_config, dict):
            action = skill_config.get("action")
            if action == "install_from_repo":
                target = skill_config.get("url") or skill_config.get("path")
                if not target:
                    logging.warning(
                        "install_from_repo requires 'url' or 'path': %s",
                        skill_config,
                    )
                return target or ""
            logging.warning(
                "Unsupported skill action %r; use a string target or "
                "install_from_repo.",
                action,
            )
            return ""
        logging.warning("Unsupported skill config entry: %r", skill_config)
        return ""

    def _install_agy_plugin(self, target: str, env: dict) -> bool:
        """Runs ``agy plugin install <target>``; returns True on success."""
        cmd = [self.agy_bin, "plugin", "install", target]
        result = self._execute_cli_command(cmd, env=env, cwd=self.fake_home)
        if result.returncode != 0:
            logging.error(
                "agy plugin install '%s' failed (rc=%s): %s",
                target, result.returncode,
                (result.stderr or result.stdout or "").strip(),
            )
            return False
        logging.info("Installed agy plugin from '%s'", target)
        return True

    def _log_installed_plugins(self):
        """Logs plugin names registered in the agy import manifest."""
        try:
            with open(self.plugin_manifest_path, "r") as f:
                manifest = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            logging.warning(
                "Could not read agy plugin manifest at %s: %s",
                self.plugin_manifest_path, e,
            )
            return
        # The manifest is ``{"imports": [{"name": ...}, ...]}``; older/other
        # shapes may use a ``plugins`` list or a name-keyed dict.
        plugins = manifest.get("imports", manifest.get("plugins", manifest))
        if isinstance(plugins, list):
            names = sorted(
                p.get("name", str(p)) if isinstance(p, dict) else str(p)
                for p in plugins
            )
        elif isinstance(plugins, dict):
            names = sorted(plugins.keys())
        else:
            names = []
        logging.info("agy registered plugins: %s", names)

    def _clone_skill_repo(self, url: str, workdir: str, env: dict):
        """Clones a skill repo. Supports ``<url>#<ref>`` pinning where
        ``<ref>`` is a branch or tag name.

        Pinning is implemented with ``git clone --depth 1 --branch <ref>``,
        which accepts branch and tag names only -- a raw commit SHA is not a
        valid ``--branch`` argument and will fail the clone (git reports the
        ref as not found). Fetching an arbitrary SHA is intentionally not
        supported: a shallow fetch-by-SHA needs server-side
        ``uploadpack.allowAnySHA1InWant``, which common hosts (e.g. GitHub)
        do not enable. Pin to a tag (or branch), not a commit SHA.

        Returns the clone directory on success, or None on failure.
        """
        clone_url, _, version_tag = url.partition("#")
        repo_name = re.sub(r"\.git$", "", clone_url.rstrip("/").split("/")[-1])
        clone_target = os.path.join(workdir, repo_name)
        if os.path.exists(clone_target):
            shutil.rmtree(clone_target)

        cmd = ["git", "clone", "--depth", "1"]
        if version_tag:
            cmd.extend(["--branch", version_tag])
        cmd.extend([clone_url, clone_target])

        try:
            result = subprocess.run(
                cmd, stdin=subprocess.DEVNULL, capture_output=True,
                text=True, check=False, env=env, timeout=120,
            )
            if result.returncode != 0:
                logging.error(
                    "Failed to clone repo '%s': %s", url, result.stderr.strip()
                )
                return None
            logging.info("Cloned agy skill repo '%s' to %s", url, clone_target)
            return clone_target
        except subprocess.TimeoutExpired:
            logging.error("Cloning repo '%s' timed out", url)
            return None

    def _setup_mcp_servers(self, mcp_servers_config: dict):
        """Writes MCP servers into ``<HOME>/.gemini/config/mcp_config.json``
        under the ``mcpServers`` key.

        The path and key are verified from the agy binary itself: the
        load-error string ``Failed to load JSON config file
        <HOME>/.gemini/config/mcp_config.json`` reveals the path, and
        the binary's struct tag ``json:"mcpServers"`` (from
        ``struct { McpServers map[string]interface {} }``) reveals the
        key. agy has no offline verification subcommand, so this step
        only writes config -- it does not confirm the server actually
        loads. Failures will surface at eval time via the transcript.
        """
        if not mcp_servers_config:
            return

        current_config = {}
        if os.path.exists(self.mcp_config_path):
            try:
                with open(self.mcp_config_path, "r") as f:
                    raw = f.read().strip()
                    if raw:
                        current_config = json.loads(raw)
            except json.JSONDecodeError:
                logging.warning(
                    "Invalid JSON in agy mcp_config at %s; overwriting.",
                    self.mcp_config_path,
                )

        existing = current_config.setdefault("mcpServers", {})
        for stale in [k for k in existing if k not in mcp_servers_config]:
            logging.info("Removing stale MCP server configuration: %s", stale)
            del existing[stale]
        for server_name, config in mcp_servers_config.items():
            existing[server_name] = self._translate_mcp_config(dict(config))
            logging.info("Configured MCP server: %s", server_name)

        with open(self.mcp_config_path, "w") as f:
            json.dump(current_config, f, indent=2)

    @staticmethod
    def _translate_mcp_config(config: dict) -> dict:
        """Normalizes a cross-harness MCP server config into agy's schema.

        Maps the common gemini-style ``httpUrl`` alias to ``serverUrl``, agy's
        native field. ``serverUrl`` and ``url`` (v1.0.5+) are accepted by agy
        directly and need no translation. Other fields like
        ``authProviderType``, ``oauth.scopes``, and stdio fields pass through
        natively.
        """
        if "httpUrl" in config and "serverUrl" not in config:
            config["serverUrl"] = config.pop("httpUrl")
        return config

    def _merged_env(self, extra: dict | None = None) -> dict:
        """Returns the process environment overlaid with the generator's
        configured env (and an optional per-call ``extra``)."""
        env = os.environ.copy()
        env.update(self.env)
        if extra:
            env.update(extra)
        return env

    @staticmethod
    def _base_agy_command(
        cli: str, prompt: str, resume: bool = False, model: str = None,
    ) -> list:
        """Builds the non-interactive ``agy -p`` argv shared by the eval
        turn path and the setup-time MCP probe.

        The model is selected with agy's ``--model`` flag (agy >=1.0.5). The
        value is an agy UI label like "Gemini 3.1 Pro (High)" (the exact
        strings ``agy models`` lists), not an API id; an unrecognized value is
        silently ignored and agy falls back to its default model. When no
        model is configured the flag is omitted and agy uses its default.
        """
        command = [cli, "-p", prompt, "--dangerously-skip-permissions"]
        if model:
            command += ["--model", model]
        if resume:
            command.append("--continue")
        return command

    def generate_internal(self, cli_cmd):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.agy_bin, str(cli_cmd))
        return self._run_agy_cli(cli_cmd)

    def _execute_cli_command(
        self, command, env=None, cwd=None
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command,
                stdin=subprocess.DEVNULL, capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=cwd if cwd else self.fake_home,
            )
        except FileNotFoundError:
            return subprocess.CompletedProcess(
                command, 127, "", f"Error: Command not found: {command[0]}"
            )
        except OSError as e:
            logging.warning("agy CLI invocation failed: %s", e)
            return subprocess.CompletedProcess(
                command, 1, "", f"An unexpected error occurred: {e}"
            )

    def _run_agy_cli(self, cli_cmd: CLICommand):
        env = self._merged_env(cli_cmd.env)
        # The executable is always this session's sandbox binary, regardless of
        # the label carried on cli_cmd.cli (the evaluator passes agent_version,
        # "agy", which is not a path).
        command = self._base_agy_command(
            self.agy_bin, cli_cmd.prompt, cli_cmd.resume, self.model
        )
        cwd = cli_cmd.cwd if cli_cmd.cwd else self.fake_home
        result = self._execute_cli_command(command, env=env, cwd=cwd)

        if result.returncode == 0:
            try:
                result.stdout = self._parse_transcript_jsonl(
                    cwd, fallback_response=result.stdout or "",
                )
            except Exception:
                logging.exception(
                    "Failed to parse agy transcript for cwd=%s", cwd,
                )

        return result

    @staticmethod
    def _unwrap_agy_mcp_args(raw_args: dict, is_mcp: bool) -> dict:
        """Returns the real MCP-tool arguments for a ``call_mcp_tool`` call.

        The wrapper's ``Arguments`` field is a JSON ``RawMessage`` -- it may
        arrive already parsed (dict) or as a JSON-encoded string. For native
        (non-MCP) tools (``is_mcp`` False) the args are returned unchanged.
        """
        if not is_mcp:
            return raw_args
        for key in ("Arguments", "arguments", "args"):
            if key in raw_args:
                inner = raw_args[key]
                if isinstance(inner, str):
                    try:
                        return json.loads(inner)
                    except (json.JSONDecodeError, ValueError):
                        return {"_raw": inner}
                if isinstance(inner, dict):
                    return inner
                return {"_raw": inner}
        return {}

    def _conversation_id_for_cwd(self, cwd: str):
        cache_path = os.path.join(
            self.app_data_dir, "cache", "last_conversations.json"
        )
        try:
            with open(cache_path, "r") as f:
                cache = json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
        if not isinstance(cache, dict):
            return None
        return cache.get(os.path.abspath(cwd))

    def _latest_log_path(self):
        """Return the path of the most recent agy cli log, or None.

        agy writes a fresh ``log/cli-<timestamp>.log`` per process and points
        the ``cli.log`` symlink at it. Since each query runs as its own agy
        subprocess, the newest log reflects the run we just made.
        """
        cli_log = os.path.join(self.app_data_dir, "cli.log")
        if os.path.exists(cli_log):
            return cli_log
        log_dir = os.path.join(self.app_data_dir, "log")
        try:
            logs = [
                os.path.join(log_dir, f) for f in os.listdir(log_dir)
                if f.endswith(".log")
            ]
        except OSError:
            return None
        if not logs:
            return None
        return max(logs, key=os.path.getmtime)

    def _detect_model_from_log(self):
        """Best-effort: read the resolved model label from the cli log.

        Returns the last ``label="..."`` agy logged for this run (see
        ``_MODEL_LABEL_RE``), or None if the log is missing/unreadable or
        carries no such line. Used only as a fallback when no model is
        configured, so any failure degrades gracefully to the default label.
        """
        log_path = self._latest_log_path()
        if not log_path:
            return None
        label = None
        try:
            with open(log_path, "r") as f:
                for line in f:
                    match = self._MODEL_LABEL_RE.search(line)
                    if match:
                        label = match.group(1)
        except OSError:
            return None
        return label

    def _read_transcript(self, conversation_id: str):
        transcript_path = os.path.join(
            self.app_data_dir, "brain", conversation_id,
            ".system_generated", "logs", "transcript.jsonl",
        )
        steps = []
        try:
            with open(transcript_path, "r") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        steps.append(json.loads(line))
                    except json.JSONDecodeError:
                        continue
        except OSError:
            return []
        return steps

    def _parse_transcript_jsonl(
        self, cwd: str, fallback_response: str = "",
    ) -> str:
        """Builds the same envelope as the old ``_parse_stream_json``
        output, sourcing tool calls and the assistant response from the
        per-conversation JSONL transcript that agy writes under
        ``<appDataDir>/brain/<uuid>/.system_generated/logs/``.

        Only the most-recent turn is reported (see _slice_current_turn).
        """
        # Transcripts don't carry token counts; downstream
        # token_consumption scorers will see zeros.
        final_obj = {"session_id": "", "response": "", "stats": {}}

        conversation_id = self._conversation_id_for_cwd(cwd)
        if not conversation_id:
            final_obj["response"] = fallback_response
            return json.dumps(final_obj, indent=2)
        final_obj["session_id"] = conversation_id

        steps = self._read_transcript(conversation_id)
        if not steps:
            final_obj["response"] = fallback_response
            return json.dumps(final_obj, indent=2)

        turn_steps = self._slice_current_turn(steps)
        (calls, result_for, response_text_parts,
         turn_start_ts, turn_end_ts) = self._pair_calls_and_results(turn_steps)

        final_obj["response"] = "\n".join(response_text_parts).strip() \
            or fallback_response

        # Approximate end-to-end latency from transcript timestamps.
        total_duration_ms = self._ms_between(turn_start_ts, turn_end_ts)

        final_obj["stats"]["models"] = self._build_models_stats(
            total_duration_ms
        )
        final_obj["stats"]["tools"] = self._build_tools_stats(
            calls, result_for
        )
        return json.dumps(final_obj, indent=2)

    def _slice_current_turn(self, steps: list) -> list:
        """Returns the steps from the last ``USER_INPUT`` onward (this turn).

        The transcript accumulates across turns when ``--continue`` is used,
        so the slice from the last ``USER_INPUT`` step is the new material
        from this invocation.
        """
        last_user_idx = max(
            (i for i, s in enumerate(steps)
             if s.get("type") == self._STEP_USER_INPUT),
            default=-1,
        )
        return steps[last_user_idx:] if last_user_idx >= 0 else steps

    def _pair_calls_and_results(self, turn_steps: list):
        """Pairs each tool call in one turn with the result step that
        immediately follows it.

        Returns ``(calls, result_for, response_text_parts, turn_start_ts,
        turn_end_ts)`` where ``calls`` is an ordered list of ``(call, ts)``
        and ``result_for`` maps a call's index to its ``(result_step, ts)``.

        agy records a tool call on a ``PLANNER_RESPONSE`` step and the
        tool's result on the *immediately following* MODEL step (MCP_TOOL
        for genuine MCP calls; VIEW_FILE / RUN_COMMAND / ... for native
        tools). We pair by strict adjacency: a call is bound only to the
        result step that directly follows the planner step that emitted it.
        The pending window is reset at every new planner step and at any
        other intervening step, so a call that never produced a runtime
        result -- e.g. a ``call_mcp_tool`` line the agent forged via
        ``run_command`` -- stays unpaired instead of stealing a later
        call's result. (An earlier FIFO scheme paired across steps and
        mis-attributed a forged MCP call to a subsequent shell-out's result.)
        """
        calls = []          # ordered (call_dict, ts)
        result_for = {}     # call index -> (result_step, ts)
        # call indices awaiting an adjacent result
        pending = collections.deque()
        response_text_parts = []
        turn_start_ts = None
        turn_end_ts = None

        for step in turn_steps:
            ts = step.get("created_at")
            if ts and turn_start_ts is None:
                turn_start_ts = ts
            if ts:
                turn_end_ts = ts

            stype = step.get("type")
            if stype == self._STEP_PLANNER_RESPONSE:
                # A new planner step ends the previous adjacency window.
                pending.clear()
                tool_calls = step.get("tool_calls") or []
                if tool_calls:
                    for call in tool_calls:
                        pending.append(len(calls))
                        calls.append((call, ts))
                else:
                    content = step.get("content")
                    if content:
                        response_text_parts.append(content)
            elif (step.get("source") == self._SOURCE_MODEL
                  and stype not in self._NON_RESULT_MODEL_TYPES):
                # A result step consumes the next call awaiting in the current
                # adjacency window. Consecutive results pair with consecutive
                # calls from the same planner step (rare multi-call case).
                if pending:
                    result_for[pending.popleft()] = (step, ts)
            else:
                # Any other intervening step breaks adjacency.
                pending.clear()

        return (calls, result_for, response_text_parts,
                turn_start_ts, turn_end_ts)

    def _build_tools_stats(self, calls: list, result_for: dict) -> dict:
        """Aggregates per-tool call/success/fail/duration counts from the
        paired calls into the ``tools`` stats envelope."""
        tools_by_name = {}
        for idx, (call, call_ts) in enumerate(calls):
            raw_name = call.get("name", "unknown")
            raw_args = call.get("args", {}) or {}
            # agy wraps every MCP invocation in the native ``call_mcp_tool``
            # tool; the real server/tool identity and arguments live in the
            # wrapper's args. Canonicalize to ``<server>__<tool>`` and surface
            # the unwrapped arguments so trajectory/parameter scorers compare
            # against the actual MCP call, not the wrapper envelope.
            is_mcp = parse_agy_mcp_tool_call(raw_name, raw_args) is not None
            tname = canonicalize_agy_tool_name(raw_name, raw_args)
            call_args = self._unwrap_agy_mcp_args(raw_args, is_mcp)
            slot = tools_by_name.setdefault(tname, {
                "count": 0, "success": 0, "fail": 0, "durationMs": 0,
                "parameters": [],
                "decisions": {
                    "accept": 0, "reject": 0, "modify": 0, "auto_accept": 0,
                },
            })
            slot["count"] += 1
            slot["parameters"].append(call_args)
            slot["decisions"]["accept"] += 1
            slot["decisions"]["auto_accept"] += 1

            duration = 0
            paired = result_for.get(idx)
            if paired:
                result_step, result_ts = paired
                done = result_step.get("status") == self._STEP_STATUS_DONE
                # A genuine MCP invocation is executed by agy's runtime and
                # recorded as a dedicated ``MCP_TOOL`` result step. If a
                # ``call_mcp_tool`` wrapper is paired with any other result
                # type, it did not actually run as an MCP call, so we do not
                # credit it as a success. This bounds (it cannot fully
                # prevent) crediting transcript lines an agent may have forged
                # via shell-outs; the authoritative attach guarantee is the
                # setup-time schema-cache check in ``_verify_mcp_runtime``.
                if (is_mcp and result_step.get("type")
                        != self._AGY_MCP_RESULT_TYPE):
                    done = False
                if done:
                    slot["success"] += 1
                else:
                    slot["fail"] += 1
                duration = self._ms_between(call_ts, result_ts)
            elif is_mcp:
                # An MCP wrapper call with no runtime result step never
                # executed -- count it as a failure rather than silently
                # leaving it neutral.
                slot["fail"] += 1
            slot["durationMs"] += duration

        return {
            "totalCalls": len(calls),
            "totalSuccess": sum(s["success"] for s in tools_by_name.values()),
            "totalFail": sum(s["fail"] for s in tools_by_name.values()),
            "totalDurationMs": sum(
                s["durationMs"] for s in tools_by_name.values()
            ),
            "decisions": {
                "accept": len(calls),
                "reject": 0,
                "modify": 0,
                "auto_accept": len(calls),
            },
            "byName": tools_by_name,
        }

    def _build_models_stats(self, total_duration_ms: int) -> dict:
        """Builds the ``models`` stats bucket for the turn.

        The transcript echoes neither a model name nor token counts, so the
        bucket is keyed by the configured model label (matching
        claude_code/codex_cli). When no model is configured, recover the
        model agy actually resolved (its default) from the cli log; fall back
        to a generic label only if even that is unavailable. Token counts are
        always zero.
        """
        model_name = (
            self.model
            or self._detect_model_from_log()
            or self._DEFAULT_MODEL_LABEL
        )
        return {
            model_name: {
                "api": {
                    "totalRequests": 1,
                    "totalErrors": 0,
                    "totalLatencyMs": total_duration_ms,
                },
                "tokens": dict(self._ZERO_TOKENS),
                "roles": {
                    "main": {
                        "totalRequests": 1,
                        "totalErrors": 0,
                        "totalLatencyMs": total_duration_ms,
                        "tokens": dict(self._ZERO_TOKENS),
                    },
                },
            }
        }

    @staticmethod
    def _ms_between(ts0: str, ts1: str) -> int:
        """Millisecond delta between two ISO-8601 timestamps, or 0 when
        either is missing or unparseable."""
        if not (ts0 and ts1):
            return 0
        try:
            t0 = dateutil.parser.isoparse(ts0)
            t1 = dateutil.parser.isoparse(ts1)
        except (ValueError, TypeError):
            return 0
        return int((t1 - t0).total_seconds() * 1000)

    @property
    def version(self) -> str:
        return self.agy_cli_version

    def parse_response(self, stdout: str) -> dict:
        if not stdout:
            return {}
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
            logging.error("Failed to parse JSON response: %s...", stdout[:100])
            return {}

    def extract_tools(self, stdout: str) -> list:
        """Extracts the list of tools used from the CLI output."""
        output_json = self.parse_response(stdout)
        try:
            return list(output_json["stats"]["tools"]["byName"].keys())
        except (KeyError, TypeError):
            return []

    def extract_skills(self, stdout: str) -> list:
        """Extracts activated skill names from the activate_skill tool."""
        output_json = self.parse_response(stdout)
        try:
            by_name = output_json["stats"]["tools"]["byName"]
            activate_calls = by_name.get("activate_skill", {})
            parameters_list = activate_calls.get("parameters", [])
            skills = []
            for params in parameters_list:
                skill_name = (
                    params.get("skill_name")
                    or params.get("skillName")
                    or params.get("skill")
                    or params.get("name")
                )
                if skill_name and skill_name not in skills:
                    skills.append(skill_name)
            return skills
        except (KeyError, TypeError):
            return []

    def safe_generate(
        self, cli_cmd: CLICommand
    ) -> subprocess.CompletedProcess:
        result = self.generate_internal(cli_cmd)
        if isinstance(result, str):
            return subprocess.CompletedProcess(
                args=[], returncode=0, stdout=result
            )

        if not result.stdout and result.returncode != 0:
            result.stderr += "\nError: Generator returned empty response."
        return result

    def create_command(
        self, cli: str, prompt: str, env: dict = None, resume: bool = False,
        session_id: str = None, cwd: str = None,
    ) -> CLICommand:
        # The executable is always this session's sandbox binary
        # (self.agy_bin); the ``cli`` argument -- the agent_version label "agy"
        # the evaluator passes -- is a display label, not a path, so it is not
        # used to launch the process. Only the per-call overrides are stored
        # here; the generator's configured ``self.env`` and the process
        # environment are layered in once at invocation time by
        # ``_run_agy_cli`` via ``_merged_env``.
        return CLICommand(cli=self.agy_bin, prompt=prompt, env=env or {},
                          resume=resume, cwd=cwd)
