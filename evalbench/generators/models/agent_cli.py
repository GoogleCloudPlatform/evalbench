from abc import abstractmethod

from .generator import QueryGenerator


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
