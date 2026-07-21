import subprocess
from abc import abstractmethod

from .generator import QueryGenerator


class AgentCliSession:
    """A multi-turn conversation session with a CLI agent.

    A session spans an entire scenario (all of its turns). The evaluator opens
    one session per scenario, calls ``send`` once per turn, and ``close`` when
    the scenario ends. This lets a generator keep a single persistent process
    alive across turns (see ``ClaudeCodeGenerator._ClaudeStreamingSession``),
    which preserves the model's prompt cache across turns -- unlike spawning a
    fresh ``--resume`` process per turn, whose cold cache corrupts token metrics.
    """

    def send(self, prompt: str) -> subprocess.CompletedProcess:
        """Runs one conversation turn and returns its (parsed) CLI result."""
        raise NotImplementedError

    def close(self) -> None:
        """Releases any resources held by the session (e.g. a live process)."""


class _PerTurnSession(AgentCliSession):
    """Default session: one fresh subprocess per turn, resuming the prior one.

    Reproduces the historical evaluator behavior exactly -- ``resume`` is set
    from the second turn onward and the session id captured from each turn's
    parsed output is threaded into the next ``create_command``. Used by every
    generator that does not override ``start_session`` (gemini_cli, codex_cli,
    agy_cli).
    """

    def __init__(self, generator: "AgentCliGenerator", env: dict | None, cwd: str | None):
        self._generator = generator
        self._env = env
        self._cwd = cwd
        self._turn = 0
        self._session_id = None

    def send(self, prompt: str) -> subprocess.CompletedProcess:
        gen = self._generator
        cli_cmd = gen.create_command(
            cli=gen.version,
            prompt=prompt,
            env=self._env,
            resume=(self._turn > 0),
            session_id=self._session_id,
            cwd=self._cwd,
        )
        result = gen.safe_generate(cli_cmd)
        if result.stdout:
            parsed = gen.parse_response(result.stdout)
            if parsed.get("session_id"):
                self._session_id = parsed["session_id"]
        self._turn += 1
        return result


class AgentCliGenerator(QueryGenerator):
    """Shared base for CLI-driven agent generators (gemini_cli, claude_code,
    codex_cli, agy_cli).

    The evaluator treats every subclass uniformly: open a session with
    ``start_session``, run each turn with ``session.send``, then read structured
    data with ``parse_response`` / ``extract_tools`` / ``extract_skills``. The
    reported agent version label is exposed via the ``version`` property.
    Membership in this class is what ``AgentEvaluator`` keys off of, so a new
    CLI generator only needs to subclass this -- no evaluator changes.
    """

    def start_session(
        self, env: dict | None = None, cwd: str | None = None
    ) -> AgentCliSession:
        """Opens a multi-turn session for one scenario.

        The default drives each turn as a fresh ``--resume`` subprocess.
        Generators that can keep a single persistent process alive across turns
        (preserving the prompt cache) should override this.
        """
        return _PerTurnSession(self, env, cwd)

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
