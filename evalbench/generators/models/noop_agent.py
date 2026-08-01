"""No-op agent generator.

Satisfies ``AgentEvaluator``'s requirement of an :class:`AgentCliGenerator`
(``agentevaluator.py`` keys off that type) while doing nothing: no CLI launch, no
API call, empty output. Used by flows that reuse the agent pipeline purely to reach
the scoring stage -- e.g. dataset-quality grading, where there is no agent run and
all the work lives in the scorer.
"""

import subprocess

from .agent_cli import AgentCliGenerator


class NoopAgentGenerator(AgentCliGenerator):
    """An ``AgentCliGenerator`` that produces no output."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "noop_agent"

    def generate_internal(self, cli_cmd):
        return ""

    @property
    def version(self) -> str:
        return "noop"

    def create_command(
        self, cli, prompt, env=None, resume=False, session_id=None, cwd=None
    ):
        return []

    def safe_generate(self, cli_cmd):
        return subprocess.CompletedProcess(
            args=[], returncode=0, stdout="", stderr=""
        )

    def parse_response(self, stdout: str) -> dict:
        return {}

    def extract_tools(self, stdout: str) -> list:
        return []

    def extract_skills(self, stdout: str) -> list:
        return []
