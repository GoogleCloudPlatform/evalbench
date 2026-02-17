from .generator import QueryGenerator
import subprocess
import os
import json
import logging
import re
import shutil


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

        self.real_home = os.environ.get('HOME', os.path.expanduser('~'))

        self.fake_home = os.path.abspath(os.path.join('.venv', 'fake_home'))
        self.gemini_home = os.path.join(self.fake_home, '.gemini')
        self.extensions_dir = os.path.join(self.gemini_home, 'extensions')

        os.makedirs(self.fake_home, exist_ok=True)
        os.makedirs(self.extensions_dir, exist_ok=True)

        self.env = querygenerator_config.get("env", {})
        self.env['HOME'] = self.fake_home

        if 'GOOGLE_APPLICATION_CREDENTIALS' not in self.env:
            default_adc = os.path.join(
                self.real_home, '.config', 'gcloud', 'application_default_credentials.json')
            if os.path.exists(default_adc):
                self.env['GOOGLE_APPLICATION_CREDENTIALS'] = default_adc

        if 'CLOUDSDK_CONFIG' not in self.env:
            self.env['CLOUDSDK_CONFIG'] = os.path.join(
                self.real_home, '.config', 'gcloud')

        self.gemini_cli_version = querygenerator_config.get(
            "gemini_cli_version", "gemini-cli")
        self.setup_config = querygenerator_config.get("setup", {})
        if self.setup_config:
            self._setup()

    def _setup(self):
        """Performs initial setup for Gemini CLI."""
        gemini_settings_path = os.path.join(self.gemini_home, "settings.json")

        if not os.path.exists(os.path.dirname(gemini_settings_path)):
            os.makedirs(os.path.dirname(gemini_settings_path), exist_ok=True)

        # Setup MCP Servers
        if "mcp_servers" in self.setup_config:
            self._setup_mcp_servers(
                self.setup_config["mcp_servers"], gemini_settings_path)

        self._setup_npm_auth()

        # Install Extensions
        if "extensions" in self.setup_config:
            self._install_extensions(self.setup_config["extensions"])

    def _setup_npm_auth(self):
        """Sets up NPM authentication for private registries in the FAKE HOME."""
        logging.info("Fetching new access token via gcloud auth command")

        try:
            result = subprocess.run(
                ["gcloud", "auth", "print-access-token"],
                check=True,
                capture_output=True,
                text=True
            )
            access_token = result.stdout.strip()
        except subprocess.CalledProcessError as e:
            logging.error(f"Failed to retrieve access token: {e.stderr}")
            return

        if not access_token:
            logging.error(
                "Error: Failed to retrieve access token. Please run 'gcloud auth login' first.")
            return

        npmrc_file = os.path.join(self.fake_home, ".npmrc")
        logging.info(f"Updating {npmrc_file} with new token...")

        registries = [
            "//us-west1-npm.pkg.dev/gemini-code-dev/gemini-code/",
            "//us-npm.pkg.dev/artifact-foundry-prod/npm-3p-trusted/",
            "//us-npm.pkg.dev/artifact-foundry-prod/ah-3p-staging-npm/"
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

        logging.info(
            f"NPM authentication updated successfully at {npmrc_file}")

    def _setup_mcp_servers(self, mcp_servers_config: dict, settings_path: str):
        """Configures MCP servers in the settings file."""
        current_settings = {}
        if os.path.exists(settings_path):
            try:
                with open(settings_path, 'r') as f:
                    current_settings = json.load(f)
            except json.JSONDecodeError:
                pass

        if "mcpServers" not in current_settings:
            current_settings["mcpServers"] = {}

        # Merge/Overwrite configurations
        for server_name, config in mcp_servers_config.items():
            if "command" not in config:
                package_name = config.get("package", server_name)

                config["command"] = "npm"
                args = config.get("args", [])

                config["args"] = ["exec", "--yes", package_name, "--"] + args

            current_settings["mcpServers"][server_name] = config

        with open(settings_path, 'w') as f:
            json.dump(current_settings, f, indent=2)

    def _install_extensions(self, extensions: dict | list):
        """Installs/Syncs specified extensions using gemini-cli."""
        if isinstance(extensions, list):
            extensions = {ext: {} for ext in set(extensions)}

        extension_names = sorted(list(extensions.keys()))

        install_env = os.environ.copy()
        install_env.update(self.env)

        installed_extensions = set()
        try:
            cmd = ["npm", "exec", "--yes", self.gemini_cli_version,
                   "--", "extensions", "list"]
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=False,
                env=install_env
            )

            for line in result.stdout.splitlines():
                line = line.strip()

                warn_match = re.search(
                    r"Warning: Skipping extension in (.*?): Configuration file not found", line)
                if warn_match:
                    corrupted_path = warn_match.group(1).strip()
                    logging.warning(
                        f"Detected corrupted extension at {corrupted_path}. Removing...")
                    try:
                        shutil.rmtree(corrupted_path)
                    except Exception as e:
                        logging.error(
                            f"Failed to remove corrupted extension directory {corrupted_path}: {e}")
                    continue

                keychain_match = re.search(
                    r"Warning: Skipping extension in (.*?): Keychain is not available", line)
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

                        name = parts[1] if "✓" in parts[0] and len(
                            parts) >= 2 else parts[0]

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
                        ["npm", "exec", "--yes", self.gemini_cli_version,
                            "--", "extensions", "uninstall", ext],
                        check=False,
                        capture_output=True,
                        env=install_env
                    )
                except Exception as e:
                    logging.warning(
                        f"Failed to uninstall extension {ext}: {e}")

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
                logging.info(
                    f"Extension '{ext}' appears to be already installed. Skipping.")
                continue

            logging.info(f"Installing extension: {ext}")

            current_ext_env = install_env.copy()
            if extensions[ext] and "settings" in extensions[ext]:
                current_ext_env.update(extensions[ext]["settings"])

            try:
                # gemini extensions install <name_or_url> --consent
                result = subprocess.run(
                    ['npm', 'exec', '--yes', self.gemini_cli_version,
                        '--', 'extensions', 'install', ext, '--consent'],
                    check=False,
                    capture_output=True,
                    text=True,
                    input='\n' * 10,
                    env=current_ext_env,
                    timeout=300
                )

                if result.returncode != 0:
                    logging.error(
                        f"Failed to install extension {ext}. Output: {result.stdout}, Error: {result.stderr}")
                else:
                    logging.info(f"Successfully installed extension: {ext}")

                ext_name_match = re.search(r"([^/]+?)(?:\.git)?$", ext)
                if ext_name_match:
                    search_name = ext_name_match.group(1)
                    if os.path.exists(self.extensions_dir):
                        for item in os.listdir(self.extensions_dir):
                            if search_name in item:
                                self._patch_manifest_sensitive(
                                    os.path.join(self.extensions_dir, item))

            except subprocess.TimeoutExpired:
                logging.error(f"Installation of extension {ext} timed out.")
            except Exception as e:
                logging.error(f"Failed to install extension {ext}: {e}")

    def _patch_manifest_sensitive(self, ext_path):
        """Patches extension manifest to disable keychain requirements."""
        manifest_path = os.path.join(ext_path, "gemini-extension.json")
        try:
            if os.path.exists(manifest_path):
                with open(manifest_path, 'r') as f:
                    content = f.read()

                if '"sensitive": true' in content:
                    logging.info(
                        f"Patching manifest at {manifest_path} for headless compatibility.")
                    content = content.replace(
                        '"sensitive": true', '"sensitive": false')
                    with open(manifest_path, 'w') as f:
                        f.write(content)
        except Exception as e:
            logging.error(f"Failed to patch manifest at {manifest_path}: {e}")

    def generate_internal(self, cli_cmd: CLICommand | str):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.gemini_cli_version, str(cli_cmd))
        return self._run_gemini_cli(cli_cmd)

    def _execute_cli_command(self, command: list[str], env: dict[str, str] | None = None) -> subprocess.CompletedProcess:
        try:
            result = subprocess.run(
                command, capture_output=True, text=True, check=False, env=env
            )
            return result
        except FileNotFoundError:
            return subprocess.CompletedProcess(command, 127, "", f"Error: Command not found: {command[0]}")
        except Exception as e:
            return subprocess.CompletedProcess(command, 1, "", f"An unexpected error occurred: {e}")

    def _run_gemini_cli(self, cli_cmd: CLICommand):
        gemini_settings_path = os.path.join(self.gemini_home, "settings.json")

        if not os.path.exists(gemini_settings_path):
            os.makedirs(os.path.dirname(gemini_settings_path), exist_ok=True)
            with open(gemini_settings_path, 'w') as f:
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

        command.extend([
            "--output-format",
            "json",
            "--prompt",
            cli_cmd.prompt,
        ])

        return self._execute_cli_command(command, env=env)

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

    def create_command(self, cli: str, prompt: str, env: dict = None, resume: bool = False) -> CLICommand:
        merged_env = self.env.copy()

        if hasattr(self, "setup_config") and "extensions" in self.setup_config:
            for _, ext_data in self.setup_config["extensions"].items():
                if ext_data and "settings" in ext_data:
                    merged_env.update(ext_data["settings"])

        if env:
            merged_env.update(env)
        return CLICommand(cli=cli, prompt=prompt, env=merged_env, resume=resume)
