from .agent_cli import AgentCliGenerator
from .tool_naming import canonicalize_agy_tool_name, parse_agy_mcp_tool_call
import subprocess
import os
import json
import logging
import re
import shutil
import sys
import tempfile
import weakref
from typing import Optional, Union, Dict, List
from util.context import rpc_id_var
from util.config import parse_timeout_seconds

# Default CLI label reported in metadata. The executed binary is installed
# per-session at self.agy_bin (see _ensure_agy_installed).
AGY_CLI = "agy"

AGY_INSTALL_URL = "https://antigravity.google/cli/install.sh"

# Makes agy authenticate from Application Default Credentials
# instead of its interactive OAuth login. See _setup_auth.
ADC_AUTH_ENV_VAR = "AGY_ADC_AUTH"

# Read-only secret mount in the GKE pod; the only ADC a pod carries on disk.
GKE_SA_KEY_PATH = "/etc/evalbench-sa-key/key.json"


def _shred_credential(path: str) -> None:
    """Deletes a temporary credential copy. Module-level and instance-free so
    weakref.finalize can hold it without keeping the generator alive."""
    try:
        os.unlink(path)
    except OSError as e:
        logging.warning("Failed to remove temporary agy ADC %s: %s", path, e)


class CLICommand:
    def __init__(self, cli, prompt, env=None, resume=False, cwd=None):
        self.cli = cli
        self.prompt = prompt
        self.env = env if env else {}
        self.resume = resume
        self.cwd = cwd


class AgyCliGenerator(AgentCliGenerator):
    """Generator that queries via the Antigravity CLI (``agy``).

    The eval turn runs ``agy -p <prompt> --dangerously-skip-permissions
    --output-format stream-json [--model <label>] [--print-timeout <timeout>]
    [--continue]``. The on-disk
    layout lives under ``~/.gemini/antigravity-cli/`` (the binary calls this
    ``appDataDir``). Skills are delivered via plugins (see _setup_skills).
    ``--output-format stream-json`` emits newline-delimited events (an
    ``init``, one ``step_update`` per step, then a final ``result``); tool
    calls, the response, token usage, and latency are all read from that
    stream (see _parse_stream_json).
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
        self.timeout = querygenerator_config.get("timeout")

        self._validate_timeout(self.timeout)

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

        # Fail fast: an unusable model or a dead MCP server otherwise degrades
        # silently to shell-outs and scores as poor model behaviour.
        configured_servers = self._configured_mcp_servers()
        if configured_servers or self.model:
            self._verify_runtime(configured_servers)

    @staticmethod
    def _validate_timeout(timeout):
        if timeout is not None:
            if not isinstance(timeout, str):
                raise TypeError(
                    "timeout must be a string (e.g., '20m', '1h30m', '300s')"
                )
            # Strict regex for common units (s, m, h).
            # Allows things like "20m", "1h30m", "300s".
            if not re.match(r"^(\d+(s|m|h))+$", timeout):
                raise ValueError(
                    f"Invalid timeout format: '{timeout}'. "
                    "Must be a valid duration string (e.g., '20m', '1h30m', '300s')."
                )

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
        # Deterministic CLI log path passed to agy via --log-file, so model
        # detection reads exactly this run's log rather than guessing the
        # newest file (which races under concurrency).
        self.cli_log_path = os.path.join(self.app_data_dir, "log", "eval-cli.log")
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
        os.makedirs(os.path.dirname(self.cli_log_path), exist_ok=True)
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
        """Stages gcloud ADC into the sandbox so the sandboxed CLI
        authenticates without an interactive login.

        ``AGY_ADC_AUTH`` is what makes agy read ADC instead of its own OAuth
        token, and is set unconditionally: the harness overrides ``HOME``, so
        the sandbox has no token and agy would otherwise block on the
        device-code URL.
        """
        self.env[ADC_AUTH_ENV_VAR] = "true"
        self._setup_gcloud_credentials(self.env, self.real_home, self.fake_home)

        # agy reads the merged env, where a shell-exported path counts just as
        # much as a config-supplied one.
        adc_path = (self.env.get("GOOGLE_APPLICATION_CREDENTIALS")
                    or os.environ.get("GOOGLE_APPLICATION_CREDENTIALS"))

        # Read in place: the session sandbox is on a PVC shared across
        # sessions, which is no place for a private key.
        if not (adc_path and os.path.exists(adc_path)) and os.path.exists(
            GKE_SA_KEY_PATH
        ):
            adc_path = GKE_SA_KEY_PATH
            self.env["GOOGLE_APPLICATION_CREDENTIALS"] = adc_path

        self.adc_path = adc_path if adc_path and os.path.exists(adc_path) else None
        if not self.adc_path:
            raise RuntimeError(
                "agy requires an application default credentials file: it "
                "reads its entitlement project from the credential's "
                "quota_project_id, which a metadata-server token does not "
                "carry. Run 'gcloud auth application-default login', set "
                "GOOGLE_APPLICATION_CREDENTIALS, or mount a service-account "
                f"key at {GKE_SA_KEY_PATH}."
            )
        logging.info("agy ADC resolved from %s", self.adc_path)
        self._ensure_quota_project()

    def _ensure_quota_project(self) -> None:
        """Adds ``quota_project_id`` to the resolved ADC when it is missing.

        agy reads its entitlement project from that field alone -- not
        GOOGLE_CLOUD_PROJECT, not GOOGLE_CLOUD_QUOTA_PROJECT (verified against
        ``agy models``: only the file field populates the registry), not
        settings.json. A stock service-account key has none, and without it
        agy's model registry comes back empty and every ``--model`` value,
        including its own default, is rejected as unknown.

        Raises on every path that cannot deliver the field: a run without it
        fails every turn.
        """
        try:
            with open(self.adc_path) as f:
                credentials = json.load(f)
        except (OSError, json.JSONDecodeError) as e:
            raise RuntimeError(
                f"agy ADC at {self.adc_path} is unreadable, so its "
                f"quota_project_id cannot be confirmed: {e}"
            ) from e

        if credentials.get("quota_project_id"):
            return

        project = self.gcp_project or credentials.get("project_id")
        if not project:
            raise RuntimeError(
                f"agy ADC {self.adc_path} has no quota_project_id and no "
                "project is configured, so agy would list no models. Set "
                "GOOGLE_CLOUD_PROJECT in the model config's 'env' block, or "
                "add quota_project_id to the credential."
            )

        credentials["quota_project_id"] = project
        # Node-local scratch: the GKE key is read in place from a read-only
        # mount, and its copy must not land on the shared-PVC sandbox.
        try:
            fd, augmented = tempfile.mkstemp(prefix="agy-adc-", suffix=".json")
            with os.fdopen(fd, "w") as f:
                json.dump(credentials, f)
        except OSError as e:
            logging.warning("Failed to write augmented agy ADC: %s", e)
            return

        self.adc_path = augmented
        self.env["GOOGLE_APPLICATION_CREDENTIALS"] = augmented
        # Plaintext key on shared scratch: bound its lifetime to the generator
        # rather than leaving one behind per session for the life of the node.
        self._adc_cleanup = weakref.finalize(self, _shred_credential, augmented)
        logging.info(
            "agy ADC had no quota_project_id; using augmented copy at %s "
            "with project %s.", augmented, project,
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
        # Reused by _setup_auth; agy's ADC path ignores this settings block.
        self.gcp_project = project

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

    def _configured_mcp_servers(self) -> list:
        return list(self.setup_config.get("mcp_servers") or {}) + list(
            self.setup_config.get("fake_mcp_servers") or {}
        )

    # stream-json event/step markers (agy --output-format stream-json).
    # Each line is one JSON object: an ``init`` event, many ``step_update``
    # events, then a final ``result`` event.
    _EVENT_RESULT = "result"
    # The ``status`` on the final ``result`` event; anything else (e.g.
    # "ERROR" from a timed-out/failed run) counts as a model error.
    _STATUS_SUCCESS = "SUCCESS"
    _STEP_TYPE_TOOL = "tool"
    # A tool step is emitted twice: ACTIVE when dispatched, then DONE
    # (success) or ERROR (failure) -- both carry the same ``step_index``.
    _STATE_DONE = "DONE"

    # cli.log line agy emits once it has resolved the model for a run, e.g.
    #   model_config_manager.go:157] Propagating selected model override to
    #   backend: label="Gemini 3.5 Flash (High)"
    # This is the only on-disk record of the *resolved* model: it appears
    # whether the model came from --model, settings.json, or agy's own default.
    # Used to label the stats bucket when no model is configured -- see
    # _detect_model_from_log.
    _MODEL_LABEL_RE = re.compile(
        r'Propagating selected model override to backend: label="([^"]+)"'
    )

    # Bucket label when the resolved model can't be determined from any source.
    _DEFAULT_MODEL_LABEL = "agy"

    # Fallback token bucket when the stream-json result carries no usage.
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

    # agy resolves --model only after it has populated the MCP schema cache, so
    # an unrecognized model leaves tool discovery looking healthy while every
    # turn exits 1 with an empty response and scores as poor model behaviour.
    _MODEL_FATAL_MARKER = "invalid model selection"

    def _verify_runtime(self, configured_servers: list):
        """Spawns a short-lived ``agy -p`` probe and confirms the configured
        model is accepted and each configured MCP server actually attached and
        discovered tools.

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
        cmd = self._base_agy_command(
            self.agy_bin, "ping", model=self.model
        )
        try:
            subprocess.run(
                cmd, env=env, cwd=self.fake_home,
                stdin=subprocess.DEVNULL, capture_output=True, text=True,
                timeout=120, check=False,
            )
        except (FileNotFoundError, subprocess.TimeoutExpired) as e:
            raise RuntimeError(
                f"agy startup probe failed to run: {e}. "
                f"Configured MCP servers: {configured_servers}.\n"
                f"STDOUT: \n{getattr(e, 'stdout', '')}\n"
                f"STDERR: \n{getattr(e, 'stderr', '')}"
            ) from e

        # Collect fatal log markers (diagnostic context for any failure).
        after = set(os.listdir(log_dir)) if os.path.isdir(log_dir) else set()
        new_logs = sorted(after - before)
        marker_hits = []
        model_hits = []
        if new_logs:
            probe_log = os.path.join(log_dir, new_logs[-1])
            try:
                with open(probe_log, "r") as f:
                    for line in f:
                        if any(m in line for m in self._MCP_FATAL_MARKERS):
                            marker_hits.append(line.rstrip())
                        if self._MODEL_FATAL_MARKER in line:
                            model_hits.append(line.rstrip())
            except OSError as e:
                logging.warning(
                    "agy startup probe log %s unreadable: %s", probe_log, e,
                )

        if model_hits:
            raise RuntimeError(
                f"agy rejected the configured model {self.model!r}. Check "
                "docs/agy_cli_agent_testing.md for ADC-supported models and "
                "accepted labels, or omit 'model' to use agy's default.\n"
                + "\n".join(f"  {h}" for h in model_hits)
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
                "(use 'httpUrl'; 'serverUrl' and 'url' also work), auth, and "
                "reachability. agy degrades silently to shell-outs when "
                "MCP tools are missing."
            )
            msg += f"\nADC in use: {self.adc_path}"
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
        loads. Failures will surface at eval time via the stream-json output.
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
        native field. ``serverUrl`` and ``url`` are accepted by agy
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
        # YAML gives ints/bools for unquoted scalars; subprocess needs strings.
        env.update({str(k): str(v) for k, v in self.env.items()})
        if extra:
            env.update({str(k): str(v) for k, v in extra.items()})
        return env

    @staticmethod
    def _base_agy_command(
        cli: str, prompt: str, resume: bool = False, model: str = None,
        output_format: str = None, log_file: str = None,
        timeout: str = None,
    ) -> list:
        """Builds the non-interactive ``agy -p`` argv shared by the eval
        turn path and the setup-time MCP probe.

        The model is selected with agy's ``--model`` flag (e.g.
        ``Gemini 3.1 Pro (Low)`` or ``gemini-3.1-pro-low``); an unrecognized
        value fails the run. When no model is configured the flag is omitted
        and agy uses its default. See docs/agy_cli_agent_testing.md.

        ``output_format`` maps to agy's ``--output-format`` (values ``json``
        and ``stream-json``); the eval turn passes ``stream-json`` to get the
        machine-readable event stream. Omitted for the setup probe.

        ``log_file`` maps to ``--log-file``, pinning the CLI log to a known
        path for deterministic model detection.

        ``timeout`` maps to ``--print-timeout`` (e.g. "20m"). If omitted,
        defaults to agy's internal default (5 minutes).
        """
        command = [cli, "-p", prompt, "--dangerously-skip-permissions"]
        if model:
            command += ["--model", model]
        if output_format:
            command += ["--output-format", output_format]
        if log_file:
            command += ["--log-file", log_file]
        if timeout:
            command += ["--print-timeout", timeout]
        if resume:
            command.append("--continue")
        return command

    def generate_internal(self, cli_cmd, timeout_seconds=None):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.agy_bin, str(cli_cmd))
        return self._run_agy_cli(cli_cmd, timeout_seconds=timeout_seconds)

    def _execute_cli_command(
        self, command, env=None, cwd=None, timeout_seconds=None
    ) -> subprocess.CompletedProcess:
        try:
            return subprocess.run(
                command,
                stdin=subprocess.DEVNULL, capture_output=True,
                text=True,
                check=False,
                env=env,
                cwd=cwd if cwd else self.fake_home,
                timeout=timeout_seconds,
            )
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
        except OSError as e:
            logging.warning("agy CLI invocation failed: %s", e)
            return subprocess.CompletedProcess(
                command, 1, "", f"An unexpected error occurred: {e}"
            )

    def _run_agy_cli(self, cli_cmd: CLICommand, timeout_seconds=None):
        env = self._merged_env(cli_cmd.env)
        # The executable is always this session's sandbox binary, regardless of
        # the label carried on cli_cmd.cli (the evaluator passes agent_version,
        # "agy", which is not a path).
        command = self._base_agy_command(
            self.agy_bin, cli_cmd.prompt, cli_cmd.resume, self.model,
            output_format="stream-json", log_file=self.cli_log_path,
            timeout=self.timeout,
        )
        cwd = cli_cmd.cwd if cli_cmd.cwd else self.fake_home
        result = self._execute_cli_command(command, env=env, cwd=cwd, timeout_seconds=timeout_seconds)

        # Parse whenever agy emitted a stream, even on a non-zero exit: a
        # timed-out/errored run still ends in a ``result`` event carrying real
        # usage tokens and the tool calls made, which we want to keep. Empty
        # stdout (e.g. binary not found) is left for safe_generate to flag.
        if result.stdout:
            try:
                result.stdout = self._parse_stream_json(result.stdout)
            except Exception:
                logging.exception("Failed to parse agy stream-json output")

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

    def _detect_model_from_log(self):
        """Best-effort: read the resolved model label from the cli log.

        Reads ``self.cli_log_path`` (pinned via ``--log-file``) and returns the
        last ``label="..."`` agy logged (see ``_MODEL_LABEL_RE``), or None if
        the log is missing/unreadable or carries no such line. Used only as a
        fallback when no model is configured, so any failure degrades
        gracefully to the default label.
        """
        label = None
        try:
            with open(self.cli_log_path, "r") as f:
                for line in f:
                    match = self._MODEL_LABEL_RE.search(line)
                    if match:
                        label = match.group(1)
        except OSError:
            return None
        return label

    def _parse_stream_json(self, stdout: str, fallback_response: str = "") -> str:
        """Builds the stats envelope from agy's ``--output-format stream-json``
        output: newline-delimited JSON events (``init``, ``step_update`` x N,
        ``result``). The stream is per-invocation, so no cross-turn slicing is
        needed.
        """
        final_obj = {"session_id": "", "response": "", "stats": {}}

        events = []
        for line in stdout.splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                continue
            # agy emits one JSON object per line, but a stray scalar/array
            # (e.g. a serialized warning) parses cleanly; skip non-objects so
            # downstream ``.get`` calls can't raise on them.
            if isinstance(event, dict):
                events.append(event)
        if not events:
            final_obj["response"] = fallback_response
            return json.dumps(final_obj, indent=2)

        result = next(
            (e["result"] for e in events
             if e.get("event") == self._EVENT_RESULT
             and isinstance(e.get("result"), dict)),
            {},
        )
        final_obj["session_id"] = (
            result.get("conversation_id")
            or next((e.get("conversation_id", "") for e in events), "")
        )
        final_obj["response"] = result.get("response") or fallback_response

        duration_ms = int(result.get("duration_seconds", 0) * 1000)
        final_obj["stats"]["models"] = self._build_models_stats(
            duration_ms, result.get("usage") or {}, result.get("status")
        )
        final_obj["stats"]["tools"] = self._build_tools_stats(
            self._collect_tool_calls(events)
        )
        return json.dumps(final_obj, indent=2)

    def _collect_tool_calls(self, events: list) -> list:
        """Collapses ``tool`` step_update events into one record per call.

        Each tool step is emitted as ACTIVE (dispatch) then DONE/ERROR
        (outcome), sharing a ``step_index``. We key on ``step_index`` so the
        terminal event's state and duration land on the same record, and
        preserve first-seen order.
        """
        by_index = {}
        order = []
        for event in events:
            step = event.get("step_update")
            if not isinstance(step, dict):
                continue
            if step.get("step_type") != self._STEP_TYPE_TOOL:
                continue
            idx = step.get("step_index")
            info = step.get("tool_info") or {}
            record = by_index.get(idx)
            if record is None:
                record = {"name": None, "args": {}, "state": None,
                          "duration_ms": 0}
                by_index[idx] = record
                order.append(idx)
            record["name"] = step.get("tool_name") or record["name"]
            if info.get("parameters") is not None:
                record["args"] = info["parameters"]
            state = step.get("state")
            if state in (self._STATE_DONE, "ERROR"):
                record["state"] = state
                record["duration_ms"] = int(
                    step.get("duration_seconds", 0) * 1000
                )
        return [by_index[i] for i in order]

    def _build_tools_stats(self, tool_calls: list) -> dict:
        """Aggregates per-tool call/success/fail/duration counts into the
        ``tools`` stats envelope. A call counts as a success iff its terminal
        state is ``DONE`` (``ERROR`` or no terminal event is a failure)."""
        tools_by_name = {}
        for call in tool_calls:
            raw_name = call["name"] or "unknown"
            raw_args = call["args"] or {}
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
            if call["state"] == self._STATE_DONE:
                slot["success"] += 1
            else:
                slot["fail"] += 1
            slot["durationMs"] += call["duration_ms"]

        return {
            "totalCalls": len(tool_calls),
            "totalSuccess": sum(s["success"] for s in tools_by_name.values()),
            "totalFail": sum(s["fail"] for s in tools_by_name.values()),
            "totalDurationMs": sum(
                s["durationMs"] for s in tools_by_name.values()
            ),
            "decisions": {
                "accept": len(tool_calls),
                "reject": 0,
                "modify": 0,
                "auto_accept": len(tool_calls),
            },
            "byName": tools_by_name,
        }

    def _build_models_stats(
        self, total_duration_ms: int, usage: dict = None, status: str = None
    ) -> dict:
        """Builds the ``models`` stats bucket for the turn.

        stream-json carries token ``usage`` but no model name, so the bucket
        is keyed by the configured model label (matching claude_code/
        codex_cli). When no model is configured, recover the model agy
        actually resolved (its default) from the cli log; fall back to a
        generic label only if even that is unavailable.

        ``status`` is the final ``result`` event's status; a present,
        non-``SUCCESS`` value (e.g. a timed-out/failed run whose stream we
        still parse) counts as one model error. A missing status is treated
        as no error, so a partial stream is not misreported as a failure.
        """
        model_name = (
            self.model
            or self._detect_model_from_log()
            or self._DEFAULT_MODEL_LABEL
        )
        tokens = self._map_usage_tokens(usage)
        errors = 1 if status and status != self._STATUS_SUCCESS else 0
        return {
            model_name: {
                "api": {
                    "totalRequests": 1,
                    "totalErrors": errors,
                    "totalLatencyMs": total_duration_ms,
                },
                "tokens": tokens,
                "roles": {
                    "main": {
                        "totalRequests": 1,
                        "totalErrors": errors,
                        "totalLatencyMs": total_duration_ms,
                        "tokens": dict(tokens),
                    },
                },
            }
        }

    def _map_usage_tokens(self, usage: dict) -> dict:
        """Maps agy's stream-json ``usage`` block onto the stats token bucket.

        agy reports input/output/thinking/total; ``cached`` and ``tool`` are
        not exposed and stay zero. ``prompt`` mirrors ``input`` and
        ``candidates`` mirrors ``output`` to match the other CLI adapters.
        """
        if not usage:
            return dict(self._ZERO_TOKENS)
        input_tokens = usage.get("input_tokens", 0)
        output_tokens = usage.get("output_tokens", 0)
        return {
            "input": input_tokens,
            "prompt": input_tokens,
            "candidates": output_tokens,
            "total": usage.get("total_tokens", 0),
            "cached": 0,
            "thoughts": usage.get("thinking_tokens", 0),
            "tool": 0,
        }

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
        self, cli_cmd: CLICommand, timeout_seconds: Optional[float] = None
    ) -> subprocess.CompletedProcess:
        result = self.generate_internal(cli_cmd, timeout_seconds=timeout_seconds)
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
