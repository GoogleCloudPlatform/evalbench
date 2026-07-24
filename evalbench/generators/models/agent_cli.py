from abc import abstractmethod

from .generator import QueryGenerator
import os
import shutil


class AgentCliGenerator(QueryGenerator):
    """Shared base for CLI-driven agent generators (gemini_cli, claude_code,
    codex_cli, agy_cli).

    The evaluator treats every subclass uniformly: build a command with
    ``create_command``, run it with ``safe_generate``, then read structured
    data with ``parse_response`` / ``extract_tools`` / ``extract_skills``. The
    reported agent version label is exposed via the ``version`` property.
    Membership in this class is what ``AgentEvaluator`` keys off of, so a new
    CLI generator only needs to subclass this -- no evaluator changes.
    """

    def _setup_gcloud_credentials(self, env: dict, real_home: str, fake_home: str):
        """Sets up gcloud credentials in the fake home directory."""
        adc_path = env.get("GOOGLE_APPLICATION_CREDENTIALS")
        if not adc_path:
            adc_path = os.path.join(
                real_home,
                ".config",
                "gcloud",
                "application_default_credentials.json",
            )
            if os.path.exists(adc_path):
                env["GOOGLE_APPLICATION_CREDENTIALS"] = adc_path

        if adc_path and os.path.exists(adc_path):
            fake_gcloud_dir = os.path.join(fake_home, ".config", "gcloud")
            os.makedirs(fake_gcloud_dir, exist_ok=True)
            fake_adc_path = os.path.join(
                fake_gcloud_dir, "application_default_credentials.json"
            )
            if os.path.abspath(adc_path) != os.path.abspath(fake_adc_path):
                shutil.copy2(adc_path, fake_adc_path)

        if "CLOUDSDK_CONFIG" not in env:
            env["CLOUDSDK_CONFIG"] = os.path.join(
                real_home, ".config", "gcloud"
            )

    @property
    @abstractmethod
    def version(self) -> str:
        raise NotImplementedError("Subclasses must implement this property")

    @abstractmethod
    def create_command(
        self, cli: str, prompt: str, env: dict = None, resume: bool = False,
        session_id: str = None, cwd: str = None,
    ):
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def safe_generate(self, cli_cmd):
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def parse_response(self, stdout: str) -> dict:
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def extract_tools(self, stdout: str) -> list:
        raise NotImplementedError("Subclasses must implement this method")

    @abstractmethod
    def extract_skills(self, stdout: str) -> list:
        raise NotImplementedError("Subclasses must implement this method")
