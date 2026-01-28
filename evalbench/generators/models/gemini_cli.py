from .generator import QueryGenerator
import subprocess
import os
import json
import logging


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
        self.gemini_cli_version = querygenerator_config.get("gemini_cli_version", "gemini-cli")

    def generate_internal(self, cli_cmd: CLICommand):
        if not isinstance(cli_cmd, CLICommand):
            cli_cmd = CLICommand(self.gemini_cli_version, str(cli_cmd))

        return self._run_gemini_cli(cli_cmd)

    def _execute_cli_command(
        self, command: list[str], env: dict[str, str] | None = None
    ) -> subprocess.CompletedProcess:
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
        gemini_settings_path = os.path.expanduser("~/.gemini/settings.json")
        if not os.path.exists(os.path.dirname(gemini_settings_path)):
            os.makedirs(os.path.dirname(gemini_settings_path))
        if not os.path.exists(gemini_settings_path):
            with open(gemini_settings_path, 'w') as f:
                json.dump({}, f)

        env = os.environ.copy()
        env.update(cli_cmd.env)
        env.update(
            {
                "GEMINI_CLI_SYSTEM_SETTINGS_PATH": gemini_settings_path,
            }
        )

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

        return self._execute_cli_command(
            command,
            env=env,
        )

    def parse_response(self, stdout: str) -> dict:
        """Parses the JSON output from Gemini CLI."""
        try:
            return json.loads(stdout)
        except json.JSONDecodeError:
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
        """Runs the generation and handles empty responses."""
        result = self.generate(cli_cmd)
        if isinstance(result, str) and not result:
            return subprocess.CompletedProcess(
                args=[cli_cmd.cli],
                returncode=1,
                stdout="",
                stderr="Error: Generator returned empty response (possibly resource exhausted).",
            )
        return result

    def create_command(self, cli: str, prompt: str, env: dict = None, resume: bool = False) -> CLICommand:
        """Creates a CLICommand object."""
        return CLICommand(cli=cli, prompt=prompt, env=env, resume=resume)
