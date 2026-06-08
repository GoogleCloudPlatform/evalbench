import json
import logging
import os
import subprocess
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.agy_cli import AgyCliGenerator, CLICommand


APP_DATA_SUBPATH = os.path.join(".gemini", "antigravity-cli")


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    """Isolates HOME under a throwaway dir so the generator builds its sandbox
    there instead of touching the real machine. Returns the host (real) home
    path for tests that need to pre-seed host-side files (settings.json, an
    on-disk oauth token, ...)."""
    real_home = tmp_path / "real_home"
    real_home.mkdir()
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(real_home))
    return real_home


@pytest.fixture(autouse=True)
def skip_agy_install(request):
    """The generator installs the agy binary into the session sandbox during
    __init__. That hits the network, so stub it to a no-op for every test --
    the binary path (self.agy_bin) is still set by _init_paths. Tests that
    exercise the real installer opt out with @pytest.mark.real_agy_install."""
    if request.node.get_closest_marker("real_agy_install"):
        yield
        return
    with patch.object(
        AgyCliGenerator, "_ensure_agy_installed", lambda self: None
    ):
        yield


@pytest.fixture
def mock_run():
    """Patches the generator's ``subprocess.run`` with a success-by-default
    mock. Tests needing custom behavior set ``side_effect``."""
    with patch("generators.models.agy_cli.subprocess.run") as m:
        m.return_value = MagicMock(returncode=0, stdout="", stderr="")
        yield m


def _install_calls(mock_run):
    """Returns the ``agy plugin install`` subprocess calls captured. The
    executable (argv[0]) is the per-session sandbox binary path, so match on
    the ``plugin install`` subcommand rather than a fixed command name."""
    return [
        c for c in mock_run.call_args_list
        if c.args and list(c.args[0][1:3]) == ["plugin", "install"]
    ]


def test_setup_single_skill_string_runs_plugin_install(mock_run, sandbox):
    """A string entry is passed straight to ``agy plugin install``."""
    target = "cloud-sql-postgresql@gemini-cli-extensions"
    generator = AgyCliGenerator({"setup": {"skills": [target]}})

    calls = _install_calls(mock_run)
    assert len(calls) == 1
    assert list(calls[0].args[0]) == [
        generator.agy_bin, "plugin", "install", "--", target,
    ]


def test_setup_multiple_skills_string_each_installed(mock_run, sandbox):
    AgyCliGenerator({"setup": {"skills": ["plugin-A", "plugin-B"]}})

    installed = [list(c.args[0])[-1] for c in _install_calls(mock_run)]
    assert installed == ["plugin-A", "plugin-B"]


def test_install_from_repo_local_path_installs_directly(
    mock_run, sandbox, tmp_path,
):
    """A local plugin directory is installed in place -- no git clone."""
    local_dir = str(tmp_path / "my-plugin")
    generator = AgyCliGenerator({})
    generator._setup_skills(
        [{"action": "install_from_repo", "path": local_dir}]
    )

    git_calls = [
        c for c in mock_run.call_args_list
        if c.args and list(c.args[0][:2]) == ["git", "clone"]
    ]
    assert git_calls == []
    calls = _install_calls(mock_run)
    assert len(calls) == 1
    assert list(calls[0].args[0]) == [
        generator.agy_bin, "plugin", "install", "--", local_dir,
    ]


def test_install_from_repo_git_url_clones_then_installs(mock_run, sandbox):
    """A git URL is cloned first, then the clone dir is plugin-installed."""
    repo_url = "https://github.com/example/agy-skill-pack.git"
    generator = AgyCliGenerator({})
    generator._setup_skills(
        [{"action": "install_from_repo", "url": repo_url}]
    )

    git_calls = [
        c for c in mock_run.call_args_list
        if c.args and list(c.args[0][:2]) == ["git", "clone"]
    ]
    assert len(git_calls) == 1
    clone_target = git_calls[0].args[0][-1]
    expected_clone = os.path.join(
        generator.app_data_dir, ".skill_clones", "agy-skill-pack"
    )
    assert clone_target == expected_clone

    calls = _install_calls(mock_run)
    assert len(calls) == 1
    assert list(calls[0].args[0]) == [
        generator.agy_bin, "plugin", "install", "--", expected_clone,
    ]


def test_clone_skill_repo_timeout_returns_none_and_clears_stale_dir(
    mock_run, sandbox, caplog,
):
    """A clone that exceeds the timeout returns None (so the skill is simply
    skipped) rather than propagating TimeoutExpired, and logs an error.

    There is no cleanup of *this* attempt's partial dir on timeout -- the
    only cleanup is the pre-clone rmtree, which clears a stale dir left by a
    prior partial clone even when the current attempt then times out.
    """
    generator = AgyCliGenerator({})
    workdir = os.path.join(generator.app_data_dir, ".skill_clones")
    os.makedirs(workdir, exist_ok=True)

    url = "https://github.com/example/agy-skill-pack.git"
    # Leftover from a prior partial clone; pre-clone cleanup must remove it.
    stale = os.path.join(workdir, "agy-skill-pack")
    os.makedirs(stale)

    mock_run.side_effect = subprocess.TimeoutExpired(
        cmd="git clone", timeout=120
    )

    with caplog.at_level(logging.ERROR):
        result = generator._clone_skill_repo(
            url, workdir, generator._merged_env()
        )

    assert result is None
    assert not os.path.exists(stale)
    assert any("timed out" in r.getMessage() for r in caplog.records)


def test_unsupported_skill_action_is_logged_not_executed(
    mock_run, sandbox, caplog,
):
    """Legacy dict actions (link/enable/disable/uninstall) are not
    supported -- only string targets and install_from_repo are. Make sure
    they don't trigger subprocess calls and that a warning is emitted."""
    generator = AgyCliGenerator({})
    with caplog.at_level(logging.WARNING):
        generator._setup_skills([
            {"action": "link", "path": "/path/to/my-skill"},
            {"action": "enable", "name": "my-skill"},
        ])

    assert mock_run.call_count == 0
    assert any("Unsupported skill action" in r.message for r in caplog.records)


def _local_agy_bin():
    """The per-session agy binary path for a local (non-eval_server) run,
    resolved against cwd. The `sandbox` fixture chdirs into the per-test tmp
    dir; keep in sync with AgyCliGenerator._init_paths."""
    return os.path.join(
        os.path.abspath(os.path.join(".venv", "fake_home_agy")),
        ".local", "bin", "agy",
    )


@pytest.mark.real_agy_install
def test_ensure_agy_installed_skips_when_binary_present(mock_run, sandbox):
    """An existing executable at the sandbox path short-circuits the install --
    no download happens."""
    agy_bin = _local_agy_bin()
    os.makedirs(os.path.dirname(agy_bin), exist_ok=True)
    with open(agy_bin, "w") as f:
        f.write("#!/bin/sh\n")
    os.chmod(agy_bin, 0o755)

    AgyCliGenerator({})

    assert mock_run.call_count == 0


@pytest.mark.real_agy_install
def test_ensure_agy_installed_downloads_then_runs_installer(mock_run, sandbox):
    """Cold sandbox: fetch the installer with curl, then run it with bash and
    an explicit --dir pointing at the session bin dir."""
    agy_bin = _local_agy_bin()

    def fake_run(cmd, *args, **kwargs):
        # The installer materializes the binary; simulate on the bash step.
        if cmd and cmd[0] == "bash":
            os.makedirs(os.path.dirname(agy_bin), exist_ok=True)
            with open(agy_bin, "w") as f:
                f.write("#!/bin/sh\n")
            os.chmod(agy_bin, 0o755)
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    gen = AgyCliGenerator({})

    cmds = [list(c.args[0]) for c in mock_run.call_args_list]
    assert cmds[0][0] == "curl"
    assert cmds[0][-1] == "https://antigravity.google/cli/install.sh"
    assert cmds[1][0] == "bash"
    assert cmds[1][cmds[1].index("--dir") + 1] == gen.bin_dir
    assert gen.agy_bin == agy_bin


@pytest.mark.real_agy_install
def test_ensure_agy_installed_raises_on_installer_failure(mock_run, sandbox):
    """A non-zero installer exit is fatal and surfaces the step + stderr."""
    mock_run.return_value = MagicMock(
        returncode=1, stdout="", stderr="network down"
    )
    with pytest.raises(RuntimeError, match="download agy installer"):
        AgyCliGenerator({})


@pytest.mark.real_agy_install
def test_ensure_agy_installed_raises_when_binary_absent_after_install(
    mock_run, sandbox,
):
    """The installer reporting success but leaving no executable is fatal --
    otherwise the failure would surface cryptically at first invocation."""
    mock_run.return_value = MagicMock(returncode=0, stdout="", stderr="")
    with pytest.raises(RuntimeError, match="no executable"):
        AgyCliGenerator({})


def test_run_command_argv_shape(mock_run, sandbox):
    """``_run_agy_cli`` must build ``agy -p <prompt>
    --dangerously-skip-permissions`` -- no legacy flags."""
    generator = AgyCliGenerator({})
    cmd = CLICommand(cli="agy", prompt="hello world")
    generator._run_agy_cli(cmd)

    sent_argv = mock_run.call_args[0][0]
    assert sent_argv == [
        generator.agy_bin, "-p", "hello world",
        "--dangerously-skip-permissions",
    ]


def test_run_command_argv_shape_with_continue(mock_run, sandbox):
    generator = AgyCliGenerator({})
    cmd = CLICommand(cli="agy", prompt="next turn", resume=True)
    generator._run_agy_cli(cmd)

    sent_argv = mock_run.call_args[0][0]
    assert sent_argv == [
        generator.agy_bin, "-p", "next turn",
        "--dangerously-skip-permissions", "--continue",
    ]


def _write_transcript_fixture(app_data_dir, cwd, conversation_id, steps):
    """Drops a transcript.jsonl + last_conversations.json mapping into the
    fake appDataDir so ``_parse_transcript_jsonl`` can pick it up."""
    cache_dir = os.path.join(app_data_dir, "cache")
    os.makedirs(cache_dir, exist_ok=True)
    with open(os.path.join(cache_dir, "last_conversations.json"), "w") as f:
        json.dump({os.path.abspath(cwd): conversation_id}, f)

    transcript_dir = os.path.join(
        app_data_dir, "brain", conversation_id,
        ".system_generated", "logs",
    )
    os.makedirs(transcript_dir, exist_ok=True)
    with open(os.path.join(transcript_dir, "transcript.jsonl"), "w") as f:
        for step in steps:
            f.write(json.dumps(step) + "\n")


def test_parse_transcript_extracts_tools_and_response(sandbox):
    generator = AgyCliGenerator({})
    cwd = generator.fake_home
    conversation_id = "abc-123"

    steps = [
        {
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-27T07:00:00Z",
            "content": "<USER_REQUEST>list /tmp</USER_REQUEST>",
        },
        {
            "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-27T07:00:01Z",
            "tool_calls": [{"name": "list_dir",
                            "args": {"DirectoryPath": "/tmp"}}],
        },
        {
            "step_index": 2, "source": "MODEL", "type": "LIST_DIRECTORY",
            "status": "DONE", "created_at": "2026-05-27T07:00:02Z",
            "content": "file1\nfile2",
        },
        {
            "step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-27T07:00:03Z",
            "content": "I listed two files for you.",
        },
    ]

    _write_transcript_fixture(
        generator.app_data_dir, cwd, conversation_id, steps,
    )

    envelope_json = generator._parse_transcript_jsonl(cwd)
    envelope = json.loads(envelope_json)

    assert envelope["session_id"] == conversation_id
    assert envelope["response"] == "I listed two files for you."
    assert "list_dir" in envelope["stats"]["tools"]["byName"]
    list_dir = envelope["stats"]["tools"]["byName"]["list_dir"]
    assert list_dir["count"] == 1
    assert list_dir["success"] == 1
    assert envelope["stats"]["tools"]["totalCalls"] == 1
    assert envelope["stats"]["tools"]["totalSuccess"] == 1

    tools = generator.extract_tools(envelope_json)
    assert tools == ["list_dir"]


def test_parse_transcript_uses_only_last_turn(sandbox):
    """When ``--continue`` is used the transcript spans multiple turns;
    only the slice from the most-recent ``USER_INPUT`` onward should be
    reported."""
    generator = AgyCliGenerator({})
    cwd = generator.fake_home
    conversation_id = "multi-turn-xyz"

    steps = [
        # ----- Turn 1 -----
        {
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-27T07:00:00Z",
            "content": "<USER_REQUEST>turn one</USER_REQUEST>",
        },
        {
            "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-27T07:00:01Z",
            "tool_calls": [{"name": "read_file", "args": {}}],
        },
        {
            "step_index": 2, "source": "MODEL", "type": "READ_FILE",
            "status": "DONE", "created_at": "2026-05-27T07:00:02Z",
            "content": "...",
        },
        {
            "step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-27T07:00:03Z",
            "content": "turn one reply",
        },
        # ----- Turn 2 -----
        {
            "step_index": 4, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-27T07:01:00Z",
            "content": "<USER_REQUEST>turn two</USER_REQUEST>",
        },
        {
            "step_index": 5, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-27T07:01:01Z",
            "tool_calls": [{"name": "write_file", "args": {}}],
        },
        {
            "step_index": 6, "source": "MODEL", "type": "WRITE_FILE",
            "status": "DONE", "created_at": "2026-05-27T07:01:02Z",
            "content": "ok",
        },
        {
            "step_index": 7, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-27T07:01:03Z",
            "content": "turn two reply",
        },
    ]

    _write_transcript_fixture(
        generator.app_data_dir, cwd, conversation_id, steps,
    )

    envelope = json.loads(generator._parse_transcript_jsonl(cwd))

    assert envelope["response"] == "turn two reply"
    by_name = envelope["stats"]["tools"]["byName"]
    assert "write_file" in by_name
    assert "read_file" not in by_name
    assert envelope["stats"]["tools"]["totalCalls"] == 1


def test_parse_transcript_no_conversation_returns_fallback(sandbox):
    generator = AgyCliGenerator({})
    envelope_json = generator._parse_transcript_jsonl(
        generator.fake_home, fallback_response="raw stdout text",
    )
    envelope = json.loads(envelope_json)
    assert envelope["response"] == "raw stdout text"
    assert envelope["session_id"] == ""


# The exact JSON-quoted arg shape agy writes for a real MCP wrapper call.
# The ``args`` payload mirrors ``REAL_ARGS`` in tool_naming_test.py -- keep the
# two in lockstep if the observed agy transcript shape changes.
_REAL_MCP_CALL = {
    "name": "call_mcp_tool",
    "args": {
        "Arguments": '{"project":"example-project"}',
        "ServerName": '"cloud-sql"',
        "ToolName": '"list_instances"',
        "toolAction": '"Listing Cloud SQL instances"',
        "toolSummary": '"List Cloud SQL instances"',
    },
}


def test_parse_transcript_genuine_mcp_call_is_canonicalized_and_succeeds(
    sandbox,
):
    """A real ``call_mcp_tool`` paired with an ``MCP_TOOL`` result step is
    canonicalized to ``<server>__<tool>``, its args are unwrapped, and it
    counts as a success."""
    generator = AgyCliGenerator({})
    cwd = generator.fake_home
    conversation_id = "mcp-genuine"

    steps = [
        {
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-29T09:00:00Z",
            "content": "<USER_REQUEST>list instances</USER_REQUEST>",
        },
        {
            "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:01Z",
            "tool_calls": [_REAL_MCP_CALL],
        },
        {
            "step_index": 2, "source": "MODEL", "type": "MCP_TOOL",
            "status": "DONE", "created_at": "2026-05-29T09:00:02Z",
            "content": "instances: ...",
        },
        {
            "step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:03Z",
            "content": "Here are your instances.",
        },
    ]

    _write_transcript_fixture(
        generator.app_data_dir, cwd, conversation_id, steps,
    )

    envelope = json.loads(generator._parse_transcript_jsonl(cwd))
    by_name = envelope["stats"]["tools"]["byName"]

    assert "cloud-sql__list_instances" in by_name
    assert "call_mcp_tool" not in by_name
    slot = by_name["cloud-sql__list_instances"]
    assert slot["count"] == 1
    assert slot["success"] == 1
    assert slot["fail"] == 0
    # The wrapper envelope is unwrapped to the real MCP arguments.
    assert slot["parameters"] == [{"project": "example-project"}]


def test_parse_transcript_forged_mcp_call_without_result_is_failed(sandbox):
    """A ``call_mcp_tool`` line with no agy-runtime ``MCP_TOOL`` result
    step -- the cheapest transcript line an agent could forge via a
    shell-out -- is not credited as a successful MCP execution."""
    generator = AgyCliGenerator({})
    cwd = generator.fake_home
    conversation_id = "mcp-forged"

    steps = [
        {
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-29T09:00:00Z",
            "content": "<USER_REQUEST>list instances</USER_REQUEST>",
        },
        # Forged: a planner line claiming an MCP call, with no MCP_TOOL
        # result step following it.
        {
            "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:01Z",
            "tool_calls": [_REAL_MCP_CALL],
        },
        {
            "step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:02Z",
            "content": "done",
        },
    ]

    _write_transcript_fixture(
        generator.app_data_dir, cwd, conversation_id, steps,
    )

    envelope = json.loads(generator._parse_transcript_jsonl(cwd))
    by_name = envelope["stats"]["tools"]["byName"]

    slot = by_name["cloud-sql__list_instances"]
    assert slot["count"] == 1
    assert slot["success"] == 0
    assert slot["fail"] == 1
    assert envelope["stats"]["tools"]["totalSuccess"] == 0


def test_parse_transcript_mcp_call_with_non_mcp_result_is_failed(sandbox):
    """A ``call_mcp_tool`` paired with a non-``MCP_TOOL`` result step (e.g.
    a real ``RUN_COMMAND`` step the agent produced) is not credited as an
    MCP success -- only agy's dedicated MCP_TOOL result proves execution."""
    generator = AgyCliGenerator({})
    cwd = generator.fake_home
    conversation_id = "mcp-wrong-result"

    steps = [
        {
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-29T09:00:00Z",
            "content": "<USER_REQUEST>list instances</USER_REQUEST>",
        },
        {
            "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:01Z",
            "tool_calls": [_REAL_MCP_CALL],
        },
        {
            "step_index": 2, "source": "MODEL", "type": "RUN_COMMAND",
            "status": "DONE", "created_at": "2026-05-29T09:00:02Z",
            "content": "gcloud output",
        },
    ]

    _write_transcript_fixture(
        generator.app_data_dir, cwd, conversation_id, steps,
    )

    envelope = json.loads(generator._parse_transcript_jsonl(cwd))
    slot = envelope["stats"]["tools"]["byName"]["cloud-sql__list_instances"]
    assert slot["count"] == 1
    assert slot["success"] == 0
    assert slot["fail"] == 1


def test_parse_transcript_adjacency_pairing_does_not_misattribute_results(
    sandbox,
):
    """A call that produced no result of its own must not steal a *later*
    call's result. Strict next-step adjacency keeps each result with the
    call it directly followed.

    This is the exact case an earlier document-order FIFO scheme got wrong:
    a forged ``call_mcp_tool`` (no MCP_TOOL result) preceding a genuine
    native ``run_command`` would consume the later RUN_COMMAND result, both
    masking the forgery's failure and stripping the real call of its result.
    """
    generator = AgyCliGenerator({})
    cwd = generator.fake_home
    conversation_id = "adjacency-pairing"

    steps = [
        {
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-29T09:00:00Z",
            "content": "<USER_REQUEST>go</USER_REQUEST>",
        },
        # Forged MCP call: no MCP_TOOL result follows it.
        {
            "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:01Z",
            "tool_calls": [_REAL_MCP_CALL],
        },
        # A separate, genuine native call followed by its own result. Under
        # FIFO the forged MCP call above would have grabbed this result.
        {
            "step_index": 2, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:02Z",
            "tool_calls": [{"name": "run_command", "args": {"Command": "ls"}}],
        },
        {
            "step_index": 3, "source": "MODEL", "type": "RUN_COMMAND",
            "status": "DONE", "created_at": "2026-05-29T09:00:03Z",
            "content": "ok",
        },
    ]

    _write_transcript_fixture(
        generator.app_data_dir, cwd, conversation_id, steps,
    )

    envelope = json.loads(generator._parse_transcript_jsonl(cwd))
    by_name = envelope["stats"]["tools"]["byName"]
    # The forged MCP call gets no result and is failed.
    assert by_name["cloud-sql__list_instances"]["success"] == 0
    assert by_name["cloud-sql__list_instances"]["fail"] == 1
    # The genuine native call keeps its own result.
    assert by_name["run_command"]["success"] == 1
    assert by_name["run_command"]["fail"] == 0
    assert envelope["stats"]["tools"]["totalSuccess"] == 1
    assert envelope["stats"]["tools"]["totalFail"] == 1


def test_parse_transcript_interleaved_native_and_mcp_calls_pair_correctly(
    sandbox,
):
    """Native call + result, then a genuine MCP call + MCP_TOOL result, all
    pair to the right call under adjacency."""
    generator = AgyCliGenerator({})
    cwd = generator.fake_home
    conversation_id = "interleaved"

    steps = [
        {
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-29T09:00:00Z",
            "content": "<USER_REQUEST>go</USER_REQUEST>",
        },
        {
            "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:01Z",
            "tool_calls": [{"name": "view_file", "args": {}}],
        },
        {
            "step_index": 2, "source": "MODEL", "type": "VIEW_FILE",
            "status": "DONE", "created_at": "2026-05-29T09:00:02Z",
            "content": "ok",
        },
        {
            "step_index": 3, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-29T09:00:03Z",
            "tool_calls": [_REAL_MCP_CALL],
        },
        {
            "step_index": 4, "source": "MODEL", "type": "MCP_TOOL",
            "status": "DONE", "created_at": "2026-05-29T09:00:04Z",
            "content": "instances",
        },
    ]

    _write_transcript_fixture(
        generator.app_data_dir, cwd, conversation_id, steps,
    )

    envelope = json.loads(generator._parse_transcript_jsonl(cwd))
    by_name = envelope["stats"]["tools"]["byName"]
    assert by_name["view_file"]["success"] == 1
    assert by_name["cloud-sql__list_instances"]["success"] == 1
    assert envelope["stats"]["tools"]["totalSuccess"] == 2


def _write_probe_log(app_data_dir, log_name, content):
    log_dir = os.path.join(app_data_dir, "log")
    os.makedirs(log_dir, exist_ok=True)
    path = os.path.join(log_dir, log_name)
    with open(path, "w") as f:
        f.write(content)
    return path


def _write_mcp_schemas(app_data_dir, server, tools):
    """Simulate agy's attach-time tool-schema cache:
    ``<appDataDir>/mcp/<server>/<tool>.json`` (one file per discovered tool).
    """
    server_dir = os.path.join(app_data_dir, "mcp", server)
    os.makedirs(server_dir, exist_ok=True)
    for tool in tools:
        with open(os.path.join(server_dir, f"{tool}.json"), "w") as f:
            f.write('{"name": "%s"}' % tool)
    return server_dir


def _write_mcp_raw_file(app_data_dir, server, filename, content):
    """Write an arbitrary file into ``<appDataDir>/mcp/<server>/`` to
    simulate a sidecar/junk file alongside (or instead of) real tool schemas.
    """
    server_dir = os.path.join(app_data_dir, "mcp", server)
    os.makedirs(server_dir, exist_ok=True)
    path = os.path.join(server_dir, filename)
    with open(path, "w") as f:
        f.write(content)
    return path


def _local_app_data_dir():
    # Mirrors AgyCliGenerator's local-run sandbox (.venv/fake_home_agy,
    # resolved against cwd). The MCP probe fires inside __init__ -- before a
    # generator instance exists -- so verify-MCP tests can't read the path off
    # the generator and recompute it here. Lands inside the per-test tmp dir
    # only because the `sandbox` fixture chdirs there; keep in sync with
    # AgyCliGenerator.fake_home.
    return os.path.join(
        os.path.abspath(os.path.join(".venv", "fake_home_agy")),
        APP_DATA_SUBPATH,
    )


def test_verify_mcp_runtime_raises_when_no_tools_attach(mock_run, sandbox):
    """A server that attaches zero tools (the silent failure mode caused
    by a wrong URL field) must raise RuntimeError so the eval doesn't
    degrade to gcloud shell-outs. The probe writes no schema files."""
    config = {
        "setup": {
            "mcp_servers": {
                "cloud-sql": {"serverUrl": "https://example.com/mcp"},
            }
        }
    }

    def fake_run(cmd, *args, **kwargs):
        # Probe runs but discovers no tools -> no schema dir written.
        _write_probe_log(_local_app_data_dir(), "cli-probe.log", "I startup\n")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    with pytest.raises(RuntimeError, match="attached no tools"):
        AgyCliGenerator(config)


def test_verify_mcp_runtime_includes_fatal_markers_in_error(mock_run, sandbox):
    """When attach fails AND the probe log has a fatal marker, the marker
    is surfaced in the error for diagnosis."""
    config = {
        "setup": {
            "mcp_servers": {
                "cloud-sql": {"serverUrl": "https://example.com/mcp"},
            }
        }
    }

    def fake_run(cmd, *args, **kwargs):
        _write_probe_log(
            _local_app_data_dir(), "cli-probe.log",
            "W0527 09:47:04 server_oauth.go:99] "
            "Account ineligible: not eligible for Antigravity.\n",
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    with pytest.raises(RuntimeError, match="Account ineligible"):
        AgyCliGenerator(config)


def test_verify_mcp_runtime_passes_when_tools_attach(mock_run, sandbox):
    """When the probe populates the tool-schema cache, setup completes."""
    config = {
        "setup": {
            "mcp_servers": {
                "cloud-sql": {"serverUrl": "https://example.com/mcp"},
            }
        }
    }

    def fake_run(cmd, *args, **kwargs):
        _write_mcp_schemas(
            _local_app_data_dir(), "cloud-sql",
            ["list_instances", "get_instance", "create_instance"],
        )
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    gen = AgyCliGenerator(config)

    assert gen.name == "agy_cli"


def test_verify_mcp_runtime_ignores_non_schema_json(mock_run, sandbox):
    """A ``*.json`` that isn't a tool schema (sidecar file, junk, or a
    non-object) must not be counted as a discovered tool -- otherwise a
    silent attach failure that happens to leave stray JSON behind would
    falsely pass verification."""
    config = {
        "setup": {
            "mcp_servers": {
                "cloud-sql": {"serverUrl": "https://example.com/mcp"},
            }
        }
    }

    def fake_run(cmd, *args, **kwargs):
        app = _local_app_data_dir()
        # A sidecar object without a name, a JSON array, and invalid JSON --
        # none of which is a tool schema.
        _write_mcp_raw_file(app, "cloud-sql", "metadata.json", '{"foo": 1}')
        _write_mcp_raw_file(app, "cloud-sql", "list.json", "[1, 2, 3]")
        _write_mcp_raw_file(app, "cloud-sql", "broken.json", "{not json")
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    with pytest.raises(RuntimeError, match="attached no tools"):
        AgyCliGenerator(config)


def test_verify_mcp_runtime_counts_only_valid_schemas(mock_run, sandbox):
    """A real tool schema sitting next to junk still passes, and only the
    valid schema is counted as a discovered tool."""
    config = {
        "setup": {
            "mcp_servers": {
                "cloud-sql": {"serverUrl": "https://example.com/mcp"},
            }
        }
    }

    def fake_run(cmd, *args, **kwargs):
        app = _local_app_data_dir()
        _write_mcp_schemas(app, "cloud-sql", ["list_instances"])
        _write_mcp_raw_file(app, "cloud-sql", "metadata.json", '{"foo": 1}')
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    gen = AgyCliGenerator(config)

    assert gen.name == "agy_cli"


def test_verify_mcp_runtime_clears_stale_schema_cache(mock_run, sandbox):
    """A stale schema dir from a previous run must not cause a false pass:
    if this run's probe writes nothing, verification must still fail."""
    # Pre-seed a stale cache before the generator runs.
    _write_mcp_schemas(_local_app_data_dir(), "cloud-sql", ["old_tool"])

    config = {
        "setup": {
            "mcp_servers": {
                "cloud-sql": {"serverUrl": "https://example.com/mcp"},
            }
        }
    }

    def fake_run(cmd, *args, **kwargs):
        # Probe attaches nothing this run.
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    with pytest.raises(RuntimeError, match="attached no tools"):
        AgyCliGenerator(config)


def test_verify_mcp_runtime_skipped_without_mcp_servers(mock_run, sandbox):
    """No MCP servers configured -> no probe, no subprocess call."""
    AgyCliGenerator({"setup": {"skills": []}})

    assert mock_run.call_count == 0


def test_verify_mcp_runtime_unreadable_probe_log_does_not_mask_failure(
    mock_run, sandbox,
):
    """If the probe log can't be read during fatal-marker enrichment, the
    OSError is swallowed and the authoritative no-tools check still fires --
    an unreadable log must never turn a real attach failure into a pass."""
    config = {
        "setup": {
            "mcp_servers": {
                "cloud-sql": {"serverUrl": "https://example.com/mcp"},
            }
        }
    }

    def fake_run(cmd, *args, **kwargs):
        # Emit the probe "log" as a *directory* so the marker-scan open()
        # raises IsADirectoryError (an OSError). No schema files are written,
        # so the attach check must still fail.
        log_dir = os.path.join(_local_app_data_dir(), "log")
        os.makedirs(os.path.join(log_dir, "cli-probe.log"), exist_ok=True)
        return MagicMock(returncode=0, stdout="", stderr="")

    mock_run.side_effect = fake_run
    with pytest.raises(RuntimeError, match="attached no tools"):
        AgyCliGenerator(config)


def test_translate_mcp_config_maps_httpurl_to_serverurl():
    """The gemini-style ``httpUrl`` alias is rewritten to agy's ``serverUrl``
    (left untranslated, agy ignores it and attaches a transportless server
    with zero tools)."""
    out = AgyCliGenerator._translate_mcp_config({"httpUrl": "https://x/mcp"})

    assert out == {"serverUrl": "https://x/mcp"}


def test_translate_mcp_config_does_not_clobber_existing_serverurl():
    """When ``serverUrl`` is already present, the canonical value wins and
    the ``httpUrl`` alias is not mapped over it."""
    out = AgyCliGenerator._translate_mcp_config(
        {"httpUrl": "https://alias/mcp", "serverUrl": "https://canonical/mcp"}
    )

    assert out["serverUrl"] == "https://canonical/mcp"


def test_translate_mcp_config_passes_native_fields_through_unchanged():
    """stdio fields and other native agy schema keys pass through verbatim --
    no Bearer-token injection or field rewriting (unlike claude_code)."""
    cfg = {
        "command": "npx",
        "args": ["-y", "server"],
        "env": {"K": "V"},
        "authProviderType": "google_credentials",
        "oauth": {"scopes": ["a", "b"]},
        "headers": {"X": "Y"},
    }

    assert AgyCliGenerator._translate_mcp_config(dict(cfg)) == cfg


def _written_settings(generator):
    with open(generator.settings_path) as f:
        return json.load(f)


def test_config_model_passed_as_flag():
    """A configured `model` (an agy UI label) is appended to the command as
    ``--model <label>`` verbatim."""
    cmd = AgyCliGenerator._base_agy_command(
        "agy", "hi", model="Gemini 3.1 Pro (High)"
    )

    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == "Gemini 3.1 Pro (High)"


def test_no_model_flag_when_unset():
    """No configured model -> no ``--model`` flag is added."""
    cmd = AgyCliGenerator._base_agy_command("agy", "hi")

    assert "--model" not in cmd


def test_run_passes_configured_model_flag(mock_run, sandbox):
    """The turn command carries the configured model via ``--model``."""
    generator = AgyCliGenerator({"model": "Gemini 3.1 Pro (High)"})
    generator._run_agy_cli(CLICommand(generator.agy_bin, "hi"))

    argv = list(mock_run.call_args_list[-1].args[0])
    assert argv[argv.index("--model") + 1] == "Gemini 3.1 Pro (High)"


def test_model_never_written_to_settings(sandbox):
    """The model is selected via the flag, not the settings.json `model`
    key -- so no `model` key is ever written there."""
    generator = AgyCliGenerator({"model": "Gemini 3.1 Pro (High)"})

    assert "model" not in _written_settings(generator)


def _stats_models(generator, cwd):
    steps = [
        {
            "step_index": 0, "source": "USER_EXPLICIT", "type": "USER_INPUT",
            "status": "DONE", "created_at": "2026-05-27T07:00:00Z",
            "content": "<USER_REQUEST>hi</USER_REQUEST>",
        },
        {
            "step_index": 1, "source": "MODEL", "type": "PLANNER_RESPONSE",
            "status": "DONE", "created_at": "2026-05-27T07:00:01Z",
            "content": "done.",
        },
    ]
    _write_transcript_fixture(generator.app_data_dir, cwd, "conv-1", steps)
    envelope = json.loads(generator._parse_transcript_jsonl(cwd))
    return envelope["stats"]["models"]


def test_models_bucket_keyed_by_configured_model(sandbox):
    """The stats models bucket is keyed by the configured model label."""
    generator = AgyCliGenerator({"model": "Gemini 3.1 Pro (High)"})
    assert "Gemini 3.1 Pro (High)" in _stats_models(
        generator, generator.fake_home
    )


def test_models_bucket_falls_back_to_agy(sandbox):
    """Without a configured model and no cli log, the bucket falls back to
    the generic 'agy' label."""
    generator = AgyCliGenerator({})
    assert "agy" in _stats_models(generator, generator.fake_home)


_MODEL_LOG_LINE = (
    'I0604 09:27:32.670492 1261111 model_config_manager.go:157] '
    'Propagating selected model override to backend: '
    'label="Gemini 3.5 Flash (Medium)"\n'
)


def _write_cli_log(generator, *lines):
    log_dir = os.path.join(generator.app_data_dir, "log")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, "cli-20260604_092731.log")
    with open(log_file, "w") as f:
        f.writelines(lines)
    cli_log = os.path.join(generator.app_data_dir, "cli.log")
    if os.path.lexists(cli_log):
        os.remove(cli_log)
    os.symlink(log_file, cli_log)


def test_detect_model_from_log(sandbox):
    """The resolved model label is recovered from the cli log."""
    generator = AgyCliGenerator({})
    _write_cli_log(generator, "noise\n", _MODEL_LOG_LINE)

    assert generator._detect_model_from_log() == "Gemini 3.5 Flash (Medium)"


def test_detect_model_from_log_takes_last_match(sandbox):
    """When the log resolves the model more than once, the last wins."""
    generator = AgyCliGenerator({})
    _write_cli_log(
        generator,
        _MODEL_LOG_LINE,
        _MODEL_LOG_LINE.replace("Gemini 3.5 Flash (Medium)", "Gemini 3.1 Pro (High)"),
    )

    assert generator._detect_model_from_log() == "Gemini 3.1 Pro (High)"


def test_detect_model_from_log_returns_none_without_log(sandbox):
    """No cli log -> no detected model."""
    generator = AgyCliGenerator({})
    assert generator._detect_model_from_log() is None


def test_models_bucket_uses_detected_default_model(sandbox):
    """Without a configured model, the bucket is keyed by the model agy
    actually resolved (read from the cli log), not the generic 'agy'."""
    generator = AgyCliGenerator({})
    _write_cli_log(generator, _MODEL_LOG_LINE)

    models = _stats_models(generator, generator.fake_home)
    assert "Gemini 3.5 Flash (Medium)" in models
    assert "agy" not in models


def test_configured_model_overrides_detected_log_model(sandbox):
    """A configured model takes precedence over whatever the log resolved."""
    generator = AgyCliGenerator({"model": "Gemini 3.1 Pro (High)"})
    _write_cli_log(generator, _MODEL_LOG_LINE)

    assert "Gemini 3.1 Pro (High)" in _stats_models(
        generator, generator.fake_home
    )


def test_oauth_token_mirrored_from_host_disk(sandbox):
    """The host's on-disk token is mirrored into the sandbox appDataDir."""
    real_app_data = sandbox / APP_DATA_SUBPATH
    real_app_data.mkdir(parents=True)
    with open(real_app_data / "antigravity-oauth-token", "w") as f:
        f.write("DISK_TOKEN")

    generator = AgyCliGenerator({})

    token_file = os.path.join(generator.app_data_dir, "antigravity-oauth-token")
    with open(token_file) as f:
        assert f.read() == "DISK_TOKEN"


def test_missing_host_token_is_non_fatal(sandbox):
    """A missing host token does not raise at init; the warning path is
    exercised and no token file is written into the sandbox."""
    generator = AgyCliGenerator({})

    token_file = os.path.join(generator.app_data_dir, "antigravity-oauth-token")
    assert not os.path.exists(token_file)
