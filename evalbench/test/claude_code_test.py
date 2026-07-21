import json
import os
import sys
from unittest.mock import MagicMock, patch, ANY

# Add parent directory to path so we can import generators
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.claude_code import ClaudeCodeGenerator
from generators.models.agent_cli import _PerTurnSession


@patch('generators.models.claude_code.os.makedirs')
@patch('generators.models.claude_code.shutil.copy2')
@patch('generators.models.claude_code.os.path.isdir', return_value=True)
def test_setup_triggers_plugin_install(mock_isdir, mock_copy, mock_makedirs, monkeypatch):
    monkeypatch.setenv("HOME", "/fake/real_home")

    config = {
        "model": "claude-opus-4-6",
        "setup": {
            "skills": [
                {
                    "action": "install_from_repo",
                    "url": "https://github.com/user/repo.git"
                }
            ]
        }
    }

    # Custom side effect for open to handle different files
    def mock_open_side_effect(filepath, mode='r', *args, **kwargs):
        mock_file = MagicMock()
        if "marketplace.json" in filepath:
            mock_file.read.return_value = '{"name": "repo", "plugins": [{"name": "my-plugin"}]}'
        elif "settings.json" in filepath:
            mock_file.read.return_value = '{}'
        else:
            mock_file.read.return_value = '{}'

        context_mock = MagicMock()
        context_mock.__enter__.return_value = mock_file
        return context_mock

    # Custom side effect for os.path.exists
    def mock_exists_side_effect(path):
        if "marketplace.json" in path:
            return True
        if "settings.json" in path:
            return True
        return False

    with (
        patch('generators.models.claude_code.open', side_effect=mock_open_side_effect),
        patch('generators.models.claude_code.os.path.exists', side_effect=mock_exists_side_effect),
        patch.object(ClaudeCodeGenerator, '_clone_marketplace_repo', return_value="/fake/real_home/.claude/plugins/marketplaces/repo") as mock_clone,
        patch.object(ClaudeCodeGenerator, '_install_plugin') as mock_install
    ):
        ClaudeCodeGenerator(config)

        mock_clone.assert_called_once()
        # Verify it called install with correct plugin ID and some env dict
        mock_install.assert_called_once_with("my-plugin@repo", ANY)


@patch('generators.models.claude_code.os.makedirs')
@patch('generators.models.claude_code.open', create=True)
def test_install_plugin_runs_init_and_install(mock_open, mock_makedirs, monkeypatch):
    monkeypatch.setenv("HOME", "/fake/real_home")

    # Mock open to return empty json for settings.json during init
    mock_open.return_value.__enter__.return_value.read.return_value = '{}'

    generator = ClaudeCodeGenerator({"model": "claude-opus-4-6"})

    with patch.object(generator, '_execute_cli_command') as mock_execute:
        mock_execute.return_value = MagicMock(returncode=0)

        generator._install_plugin("my-plugin")

        assert mock_execute.call_count == 2

        # First call: init
        first_call_args = mock_execute.call_args_list[0][0][0]
        assert "initialize_session" in first_call_args
        assert "-p" in first_call_args

        # Second call: install
        second_call_args = mock_execute.call_args_list[1][0][0]
        assert "plugins" in second_call_args
        assert "install" in second_call_args
        assert "my-plugin" in second_call_args


class _FakeStdin:
    def __init__(self):
        self.writes = []
        self.closed = False

    def write(self, s):
        self.writes.append(s)

    def flush(self):
        pass

    def close(self):
        self.closed = True


class _FakePopen:
    """A subprocess.Popen stand-in whose stdout is a single iterator spanning
    every turn, so consecutive session.send() calls resume where the last left
    off -- exactly how one persistent process serves multiple turns."""

    def __init__(self, stdout_lines):
        self.stdin = _FakeStdin()
        self.stdout = iter(stdout_lines)
        self.stderr = iter([])
        self.killed = False

    def wait(self, timeout=None):
        return 0

    def kill(self):
        self.killed = True


def _result_line(session_id, cache_read, cache_creation, model="claude-opus-4-6"):
    return json.dumps({
        "type": "result",
        "session_id": session_id,
        "duration_ms": 1000,
        "total_cost_usd": 0.01,
        "usage": {
            "input_tokens": 100,
            "output_tokens": 10,
            "cache_read_input_tokens": cache_read,
            "cache_creation_input_tokens": cache_creation,
        },
    }) + "\n"


@patch('generators.models.claude_code.os.makedirs')
@patch('generators.models.claude_code.open', create=True)
def test_streaming_session_single_process_across_turns(mock_open, mock_makedirs, monkeypatch):
    """A multi-turn scenario runs in ONE process: turn 2 reads from the prompt
    cache (cache_creation ~ 0) instead of re-paying a cold cache."""
    monkeypatch.setenv("HOME", "/fake/real_home")
    mock_open.return_value.__enter__.return_value.read.return_value = '{}'

    generator = ClaudeCodeGenerator({"model": "claude-opus-4-6"})

    stdout_lines = [
        # Turn 1: cold cache -> large cache_creation, zero cache_read.
        json.dumps({"type": "system", "subtype": "init",
                    "session_id": "sess-1", "model": "claude-opus-4-6"}) + "\n",
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hi"}]}}) + "\n",
        _result_line("sess-1", cache_read=0, cache_creation=500),
        # Turn 2: warm cache -> reads the cached context, no new creation.
        json.dumps({"type": "assistant",
                    "message": {"content": [{"type": "text", "text": "again"}]}}) + "\n",
        _result_line("sess-1", cache_read=500, cache_creation=0),
    ]
    fake = _FakePopen(stdout_lines)

    session = generator.start_session(env={}, cwd="/tmp/work")
    with patch('generators.models.claude_code.subprocess.Popen', return_value=fake) as mock_popen:
        r1 = session.send("turn one")
        r2 = session.send("turn two")
        session.close()

    # Exactly one process serves both turns.
    assert mock_popen.call_count == 1

    # The launch uses streaming input, not `-p <prompt>` or `--resume`.
    argv = mock_popen.call_args[0][0]
    assert "--input-format" in argv
    assert argv[argv.index("--input-format") + 1] == "stream-json"
    assert "--resume" not in argv

    # Each turn was delivered as one NDJSON user message on stdin.
    assert len(fake.stdin.writes) == 2
    msg1 = json.loads(fake.stdin.writes[0])
    assert msg1 == {"type": "user",
                    "message": {"role": "user", "content": "turn one"}}
    assert json.loads(fake.stdin.writes[1])["message"]["content"] == "turn two"

    # Token metrics reflect the cache actually being reused on turn 2.
    t1 = json.loads(r1.stdout)["stats"]["models"]["claude-opus-4-6"]["tokens"]
    assert t1["cache_creation"] == 500
    assert t1["cached"] == 0
    t2 = json.loads(r2.stdout)["stats"]["models"]["claude-opus-4-6"]["tokens"]
    assert t2["cache_creation"] == 0
    assert t2["cached"] == 500

    # close() ends the session by closing stdin (EOF).
    assert fake.stdin.closed


@patch('generators.models.claude_code.os.makedirs')
@patch('generators.models.claude_code.open', create=True)
def test_streaming_session_process_death_surfaces_error(mock_open, mock_makedirs, monkeypatch):
    """If the process dies before a `result` event, send() returns a non-zero
    result with an error message rather than a bogus empty success."""
    monkeypatch.setenv("HOME", "/fake/real_home")
    mock_open.return_value.__enter__.return_value.read.return_value = '{}'

    generator = ClaudeCodeGenerator({"model": "claude-opus-4-6"})
    # stdout ends immediately (EOF) with no `result` event.
    fake = _FakePopen([])

    session = generator.start_session()
    with patch('generators.models.claude_code.subprocess.Popen', return_value=fake):
        result = session.send("hello")
        session.close()

    assert result.returncode == 1
    assert "Generator returned empty response" in result.stderr


def test_per_turn_session_resumes_and_threads_session_id():
    """The default (non-claude) session still spawns one --resume subprocess per
    turn, threading each turn's session id into the next."""
    gen = MagicMock()
    gen.version = "gemini-cli"
    gen.create_command.side_effect = lambda **kwargs: kwargs
    gen.safe_generate.side_effect = [
        MagicMock(stdout="out1"), MagicMock(stdout="out2")]
    gen.parse_response.side_effect = [{"session_id": "S1"}, {"session_id": "S2"}]

    session = _PerTurnSession(gen, env={"A": "B"}, cwd="/w")
    session.send("p1")
    session.send("p2")

    calls = gen.create_command.call_args_list
    assert calls[0].kwargs["resume"] is False
    assert calls[0].kwargs["session_id"] is None
    assert calls[1].kwargs["resume"] is True
    assert calls[1].kwargs["session_id"] == "S1"
