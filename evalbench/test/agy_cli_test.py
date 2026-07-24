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

# Sample agy UI model label used throughout the tests.
_MODEL_LABEL = "Gemini 3.1 Pro (High)"


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
    target = "/path/to/local-plugin"
    generator = AgyCliGenerator({"setup": {"skills": [target]}})

    calls = _install_calls(mock_run)
    assert len(calls) == 1
    assert list(calls[0].args[0]) == [
        generator.agy_bin, "plugin", "install", target,
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
        generator.agy_bin, "plugin", "install", local_dir,
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
        generator.agy_bin, "plugin", "install", expected_clone,
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
    --dangerously-skip-permissions --output-format stream-json``."""
    generator = AgyCliGenerator({})
    cmd = CLICommand(cli="agy", prompt="hello world")
    generator._run_agy_cli(cmd)

    sent_argv = mock_run.call_args[0][0]
    assert sent_argv == [
        generator.agy_bin, "-p", "hello world",
        "--dangerously-skip-permissions", "--output-format", "stream-json",
        "--log-file", generator.cli_log_path,
    ]


def test_run_command_argv_shape_with_continue(mock_run, sandbox):
    generator = AgyCliGenerator({})
    cmd = CLICommand(cli="agy", prompt="next turn", resume=True)
    generator._run_agy_cli(cmd)

    sent_argv = mock_run.call_args[0][0]
    assert sent_argv == [
        generator.agy_bin, "-p", "next turn",
        "--dangerously-skip-permissions", "--output-format", "stream-json",
        "--log-file", generator.cli_log_path, "--continue",
    ]


def test_init_raises_on_non_string_timeout(sandbox):
    """The generator should raise TypeError if timeout is not a string."""
    with pytest.raises(TypeError, match="timeout must be a string"):
        AgyCliGenerator({"timeout": 20})


def test_init_raises_on_invalid_timeout_format(sandbox):
    """The generator should raise ValueError if timeout format is invalid."""
    invalid_timeouts = ["20", "20 minutes", "20d", "abc", "m", "20m10"]
    for timeout in invalid_timeouts:
        with pytest.raises(ValueError, match="Invalid timeout format"):
            AgyCliGenerator({"timeout": timeout})


def test_init_accepts_valid_timeout_formats(sandbox):
    """The generator should accept valid timeout formats without raising."""
    valid_timeouts = ["20m", "300s", "1h30m", "1h", "10s"]
    for timeout in valid_timeouts:
        gen = AgyCliGenerator({"timeout": timeout})
        assert gen.timeout == timeout


def test_run_command_argv_shape_with_timeout(mock_run, sandbox):
    generator = AgyCliGenerator({"timeout": "20m"})
    cmd = CLICommand(cli="agy", prompt="hello world")
    generator._run_agy_cli(cmd)

    sent_argv = mock_run.call_args[0][0]
    assert sent_argv == [
        generator.agy_bin, "-p", "hello world",
        "--dangerously-skip-permissions", "--output-format", "stream-json",
        "--log-file", generator.cli_log_path, "--print-timeout", "20m",
    ]


def test_run_agy_cli_parses_stream_on_nonzero_exit(mock_run, sandbox):
    """A timed-out/errored run exits non-zero but still emits a full stream
    ending in an ERROR result -- its usage tokens and tool calls must be kept,
    not dropped as raw JSONL that parse_response chokes on."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})
    stream = _stream(
        _init_event("conv-timeout"),
        *_tool_events(1, "call_mcp_tool", _MCP_PARAMS),
        _result_event(
            conversation_id="conv-timeout", status="ERROR", response="",
            usage={"input_tokens": 100, "output_tokens": 20,
                   "thinking_tokens": 5, "total_tokens": 125},
        ),
    )
    mock_run.return_value = MagicMock(
        returncode=1, stdout=stream, stderr="",
    )

    result = generator._run_agy_cli(CLICommand(cli="agy", prompt="hi"))

    envelope = generator.parse_response(result.stdout)
    assert envelope["session_id"] == "conv-timeout"
    tokens = envelope["stats"]["models"][_MODEL_LABEL]["tokens"]
    assert tokens["total"] == 125
    assert generator.extract_tools(result.stdout) == ["cloud-sql__list_instances"]


def _stream(*events) -> str:
    """Serializes stream-json events to the newline-delimited stdout agy emits
    under ``--output-format stream-json``."""
    return "\n".join(json.dumps(e) for e in events)


def _init_event(conversation_id="conv-1"):
    return {"event": "init", "conversation_id": conversation_id, "init": {}}


def _tool_events(step_index, name, parameters, state="DONE",
                 duration_seconds=0.1):
    """An ACTIVE dispatch event followed by its DONE/ERROR terminal event,
    sharing ``step_index`` -- the shape agy emits for one tool call."""
    active = {
        "event": "step_update",
        "step_update": {
            "step_index": step_index, "state": "ACTIVE", "step_type": "tool",
            "tool_name": name,
            "tool_info": {"name": name, "parameters": parameters},
        },
    }
    terminal_info = {"name": name, "parameters": parameters}
    if state == "DONE":
        terminal_info["output"] = "ok"
    else:
        terminal_info["error"] = {"type": "TOOL_ERROR", "message": "boom"}
    terminal = {
        "event": "step_update",
        "step_update": {
            "step_index": step_index, "state": state, "step_type": "tool",
            "tool_name": name, "duration_seconds": duration_seconds,
            "tool_info": terminal_info,
        },
    }
    return [active, terminal]


def _result_event(conversation_id="conv-1", status="SUCCESS", response="done",
                  duration_seconds=1.0, usage=None):
    return {
        "event": "result",
        "result": {
            "conversation_id": conversation_id, "status": status,
            "response": response, "duration_seconds": duration_seconds,
            "num_turns": 1, "usage": usage or {},
        },
    }


# The MCP wrapper shape agy emits in stream-json. Unlike the transcript, the
# args arrive already parsed (ServerName/ToolName are plain strings, Arguments
# is a dict), not JSON-quoted.
_MCP_PARAMS = {
    "Arguments": {"project": "example-project"},
    "ServerName": "cloud-sql",
    "ToolName": "list_instances",
}


def test_parse_stream_json_extracts_tools_and_response(sandbox):
    generator = AgyCliGenerator({})

    stdout = _stream(
        _init_event(),
        *_tool_events(1, "list_dir", {"DirectoryPath": "/tmp"}),
        _result_event(response="I listed two files for you."),
    )

    envelope_json = generator._parse_stream_json(stdout)
    envelope = json.loads(envelope_json)

    assert envelope["session_id"] == "conv-1"
    assert envelope["response"] == "I listed two files for you."
    list_dir = envelope["stats"]["tools"]["byName"]["list_dir"]
    assert list_dir["count"] == 1
    assert list_dir["success"] == 1
    assert envelope["stats"]["tools"]["totalCalls"] == 1
    assert envelope["stats"]["tools"]["totalSuccess"] == 1

    assert generator.extract_tools(envelope_json) == ["list_dir"]


def test_parse_stream_json_skips_non_object_lines(sandbox):
    """A stray scalar/array line parses as valid JSON but is not an event;
    it must be skipped so it can't break parsing of the real events around it."""
    generator = AgyCliGenerator({})
    stdout = "\n".join([
        json.dumps(_init_event()),
        '"a warning string"',
        "[]",
        "42",
        *[json.dumps(e)
          for e in _tool_events(1, "list_dir", {"DirectoryPath": "/tmp"})],
        json.dumps(_result_event(response="done")),
    ])

    envelope = json.loads(generator._parse_stream_json(stdout))

    assert envelope["session_id"] == "conv-1"
    assert envelope["response"] == "done"
    assert envelope["stats"]["tools"]["byName"]["list_dir"]["success"] == 1


def test_parse_stream_json_skips_non_dict_nested_payloads(sandbox):
    """An event can be a dict while its ``result``/``step_update`` payload is a
    non-dict (e.g. ``{"event":"result","result":[]}``). Those payloads reach a
    ``.get()`` call, so they must be type-checked -- otherwise parsing the whole
    stream aborts and drops the valid usage/tool events around them."""
    generator = AgyCliGenerator({})
    stdout = _stream(
        _init_event(),
        {"event": "step_update", "step_update": "a warning"},
        *_tool_events(1, "list_dir", {"DirectoryPath": "/tmp"}),
        {"event": "result", "result": []},
        _result_event(response="done"),
    )

    envelope = json.loads(generator._parse_stream_json(stdout))

    assert envelope["session_id"] == "conv-1"
    assert envelope["response"] == "done"
    assert envelope["stats"]["tools"]["byName"]["list_dir"]["success"] == 1


def test_parse_stream_json_no_events_returns_fallback(sandbox):
    generator = AgyCliGenerator({})
    envelope = json.loads(
        generator._parse_stream_json("", fallback_response="raw stdout text")
    )
    assert envelope["response"] == "raw stdout text"
    assert envelope["session_id"] == ""


def test_parse_stream_json_missing_result_uses_fallback_response(sandbox):
    """A truncated stream with tool events but no ``result`` event still
    surfaces tool stats and falls back to the raw response."""
    generator = AgyCliGenerator({})
    stdout = _stream(
        _init_event("conv-x"),
        *_tool_events(1, "view_file", {"AbsolutePath": "/x"}),
    )
    envelope = json.loads(
        generator._parse_stream_json(stdout, fallback_response="partial")
    )
    assert envelope["session_id"] == "conv-x"
    assert envelope["response"] == "partial"
    assert envelope["stats"]["tools"]["byName"]["view_file"]["success"] == 1


def test_parse_stream_json_tokens_from_usage(sandbox):
    """Real token counts flow from the result event's ``usage`` block into the
    models bucket (input mirrors prompt, output mirrors candidates)."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})
    stdout = _stream(
        _init_event(),
        _result_event(usage={
            "input_tokens": 100, "output_tokens": 20,
            "thinking_tokens": 5, "total_tokens": 125,
        }),
    )
    envelope = json.loads(generator._parse_stream_json(stdout))
    tokens = envelope["stats"]["models"][_MODEL_LABEL]["tokens"]
    assert tokens == {
        "input": 100, "prompt": 100, "candidates": 20,
        "total": 125, "cached": 0, "thoughts": 5, "tool": 0,
    }


def test_parse_stream_json_success_status_reports_no_error(sandbox):
    """A SUCCESS result keeps totalErrors at 0 in both api and roles.main."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})
    stdout = _stream(_init_event(), _result_event(status="SUCCESS"))
    model = json.loads(
        generator._parse_stream_json(stdout)
    )["stats"]["models"][_MODEL_LABEL]
    assert model["api"]["totalErrors"] == 0
    assert model["roles"]["main"]["totalErrors"] == 0


def test_parse_stream_json_error_status_reports_error(sandbox):
    """A non-SUCCESS result (e.g. a timed-out/failed run) counts as one model
    error in both api and roles.main, while stats are still retained."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})
    stdout = _stream(_init_event(), _result_event(status="ERROR"))
    model = json.loads(
        generator._parse_stream_json(stdout)
    )["stats"]["models"][_MODEL_LABEL]
    assert model["api"]["totalErrors"] == 1
    assert model["roles"]["main"]["totalErrors"] == 1


def test_parse_stream_json_missing_status_reports_no_error(sandbox):
    """A partial stream with no result status is not misreported as a failure."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})
    stdout = _stream(
        _init_event(),
        *_tool_events(1, "view_file", {"AbsolutePath": "/x"}),
    )
    model = json.loads(
        generator._parse_stream_json(stdout)
    )["stats"]["models"][_MODEL_LABEL]
    assert model["api"]["totalErrors"] == 0
    assert model["roles"]["main"]["totalErrors"] == 0


def test_parse_stream_json_mcp_call_is_canonicalized_and_succeeds(sandbox):
    """A ``call_mcp_tool`` wrapper whose terminal state is DONE is
    canonicalized to ``<server>__<tool>``, its args are unwrapped, and it
    counts as a success."""
    generator = AgyCliGenerator({})
    stdout = _stream(
        _init_event(),
        *_tool_events(1, "call_mcp_tool", _MCP_PARAMS, state="DONE"),
        _result_event(),
    )

    envelope = json.loads(generator._parse_stream_json(stdout))
    by_name = envelope["stats"]["tools"]["byName"]

    assert "cloud-sql__list_instances" in by_name
    assert "call_mcp_tool" not in by_name
    slot = by_name["cloud-sql__list_instances"]
    assert slot["count"] == 1
    assert slot["success"] == 1
    assert slot["fail"] == 0
    assert slot["parameters"] == [{"project": "example-project"}]


def test_parse_stream_json_mcp_call_error_is_failed(sandbox):
    """A ``call_mcp_tool`` whose terminal state is ERROR is not credited as a
    success -- the state is authoritative, no result-type heuristic needed."""
    generator = AgyCliGenerator({})
    stdout = _stream(
        _init_event(),
        *_tool_events(1, "call_mcp_tool", _MCP_PARAMS, state="ERROR"),
        _result_event(status="ERROR", response=""),
    )

    envelope = json.loads(generator._parse_stream_json(stdout))
    slot = envelope["stats"]["tools"]["byName"]["cloud-sql__list_instances"]
    assert slot["count"] == 1
    assert slot["success"] == 0
    assert slot["fail"] == 1
    assert envelope["stats"]["tools"]["totalSuccess"] == 0


def test_parse_stream_json_tool_without_terminal_is_failed(sandbox):
    """A tool that only ever reaches ACTIVE (no DONE/ERROR) counts as a
    failure, not a neutral entry."""
    generator = AgyCliGenerator({})
    active_only = {
        "event": "step_update",
        "step_update": {
            "step_index": 1, "state": "ACTIVE", "step_type": "tool",
            "tool_name": "run_command",
            "tool_info": {"name": "run_command",
                          "parameters": {"CommandLine": "ls"}},
        },
    }
    stdout = _stream(_init_event(), active_only, _result_event())

    envelope = json.loads(generator._parse_stream_json(stdout))
    slot = envelope["stats"]["tools"]["byName"]["run_command"]
    assert slot["count"] == 1
    assert slot["success"] == 0
    assert slot["fail"] == 1


def test_parse_stream_json_multiple_tools_aggregate(sandbox):
    """Native and MCP calls each pair to their own terminal state by
    step_index, and durations/totals aggregate."""
    generator = AgyCliGenerator({})
    stdout = _stream(
        _init_event(),
        *_tool_events(1, "view_file", {"AbsolutePath": "/x"},
                      state="DONE", duration_seconds=0.2),
        *_tool_events(2, "call_mcp_tool", _MCP_PARAMS,
                      state="DONE", duration_seconds=0.5),
        _result_event(),
    )

    envelope = json.loads(generator._parse_stream_json(stdout))
    tools = envelope["stats"]["tools"]
    assert tools["byName"]["view_file"]["success"] == 1
    assert tools["byName"]["cloud-sql__list_instances"]["success"] == 1
    assert tools["totalCalls"] == 2
    assert tools["totalSuccess"] == 2
    assert tools["totalDurationMs"] == 700


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
        "agy", "hi", model=_MODEL_LABEL
    )

    assert "--model" in cmd
    assert cmd[cmd.index("--model") + 1] == _MODEL_LABEL


def test_no_model_flag_when_unset():
    """No configured model -> no ``--model`` flag is added."""
    cmd = AgyCliGenerator._base_agy_command("agy", "hi")

    assert "--model" not in cmd


def test_run_passes_configured_model_flag(mock_run, sandbox):
    """The turn command carries the configured model via ``--model``."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})
    generator._run_agy_cli(CLICommand(generator.agy_bin, "hi"))

    argv = list(mock_run.call_args_list[-1].args[0])
    assert argv[argv.index("--model") + 1] == _MODEL_LABEL


def test_model_never_written_to_settings(sandbox):
    """The model is selected via the flag, not the settings.json `model`
    key -- so no `model` key is ever written there."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})

    assert "model" not in _written_settings(generator)


def _stats_models(generator):
    envelope = json.loads(
        generator._parse_stream_json(_stream(_init_event(), _result_event()))
    )
    return envelope["stats"]["models"]


def test_models_bucket_keyed_by_configured_model(sandbox):
    """The stats models bucket is keyed by the configured model label."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})
    assert _MODEL_LABEL in _stats_models(generator)


def test_models_bucket_falls_back_to_agy(sandbox):
    """Without a configured model and no cli log, the bucket falls back to
    the generic 'agy' label."""
    generator = AgyCliGenerator({})
    assert "agy" in _stats_models(generator)


_MODEL_LOG_LINE = (
    'I0604 09:27:32.670492 1261111 model_config_manager.go:157] '
    'Propagating selected model override to backend: '
    'label="Gemini 3.5 Flash (Medium)"\n'
)


def _write_cli_log(generator, *lines):
    os.makedirs(os.path.dirname(generator.cli_log_path), exist_ok=True)
    with open(generator.cli_log_path, "w") as f:
        f.writelines(lines)


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
        _MODEL_LOG_LINE.replace("Gemini 3.5 Flash (Medium)", _MODEL_LABEL),
    )

    assert generator._detect_model_from_log() == _MODEL_LABEL


def test_detect_model_from_log_returns_none_without_log(sandbox):
    """No cli log -> no detected model."""
    generator = AgyCliGenerator({})
    assert generator._detect_model_from_log() is None


def test_models_bucket_uses_detected_default_model(sandbox):
    """Without a configured model, the bucket is keyed by the model agy
    actually resolved (read from the cli log), not the generic 'agy'."""
    generator = AgyCliGenerator({})
    _write_cli_log(generator, _MODEL_LOG_LINE)

    models = _stats_models(generator)
    assert "Gemini 3.5 Flash (Medium)" in models
    assert "agy" not in models


def test_configured_model_overrides_detected_log_model(sandbox):
    """A configured model takes precedence over whatever the log resolved."""
    generator = AgyCliGenerator({"model": _MODEL_LABEL})
    _write_cli_log(generator, _MODEL_LOG_LINE)

    assert _MODEL_LABEL in _stats_models(generator)


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
