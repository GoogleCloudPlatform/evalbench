import json
import os
import pwd
import sys
from unittest.mock import MagicMock, patch, ANY

import pytest

# Add parent directory to path so we can import generators
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.claude_code import ClaudeCodeGenerator, _chown_tree


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


TOOL_USE_EVENT = {
    "type": "assistant",
    "message": {
        "content": [
            {
                "type": "tool_use",
                "id": "toolu_01",
                "name": "mcp__cloud-sql__list_instances",
                "input": {"project": "p"},
            }
        ]
    },
}

TOOL_RESULT_EVENT = {
    "type": "user",
    "message": {
        "content": [
            {"type": "tool_result", "tool_use_id": "toolu_01", "content": "ok"}
        ]
    },
}

RESULT_EVENT = {"type": "result", "session_id": "s1", "usage": {}}


def _stamp(events_at):
    started_at_ms, tool_durations = {}, {}
    for event, arrival_ms in events_at:
        ClaudeCodeGenerator._stamp_tool_event(
            json.dumps(event), arrival_ms, started_at_ms, tool_durations)
    return tool_durations


def test_stamp_tool_event_measures_gap_between_use_and_result():
    assert _stamp([(TOOL_USE_EVENT, 1000.0)]) == {}
    assert _stamp(
        [(TOOL_USE_EVENT, 1000.0), (TOOL_RESULT_EVENT, 1250.0)]
    ) == {"toolu_01": 250}


def test_stamp_tool_event_handles_top_level_tool_result():
    top_level = {"type": "tool_result", "tool_use_id": "toolu_01"}
    assert _stamp(
        [(TOOL_USE_EVENT, 1000.0), (top_level, 1100.0)]
    ) == {"toolu_01": 100}


def test_stamp_tool_event_pairs_parallel_calls_by_id():
    parallel_use = {
        "type": "assistant",
        "message": {
            "content": [
                {"type": "tool_use", "id": "toolu_01", "name": "a"},
                {"type": "tool_use", "id": "toolu_02", "name": "b"},
            ]
        },
    }

    def result_for(tool_id):
        return {
            "type": "user",
            "message": {
                "content": [{"type": "tool_result", "tool_use_id": tool_id}]
            },
        }

    assert _stamp([
        (parallel_use, 1000.0),
        (result_for("toolu_02"), 1100.0),
        (result_for("toolu_01"), 1300.0),
    ]) == {"toolu_01": 300, "toolu_02": 100}


def test_stamp_tool_event_ignores_unpaired_and_malformed_lines():
    orphan_result = {"type": "user", "message": {
        "content": [{"type": "tool_result", "tool_use_id": "unknown"}]}}
    assert _stamp([(orphan_result, 1000.0)]) == {}

    started_at_ms, tool_durations = {}, {}
    for line in ("", "   ", "not json", "[]"):
        ClaudeCodeGenerator._stamp_tool_event(
            line, 1000.0, started_at_ms, tool_durations)
    assert tool_durations == {}


def test_streaming_timeout_keeps_stderr_diagnostics():
    """The non-streaming path appends captured stderr to the timeout message;
    dropping it here loses the CLI's own reason for hanging."""
    generator = object.__new__(ClaudeCodeGenerator)
    generator.fake_home = os.getcwd()

    result, _ = ClaudeCodeGenerator._execute_cli_streaming(
        generator,
        ["sh", "-c", "echo 'rate limit reached' >&2; sleep 30"],
        timeout_seconds=1,
    )

    assert result.returncode == 124
    assert "TimeoutError: Command timed out after 1 seconds" in result.stderr
    assert "rate limit reached" in result.stderr


def test_stamp_tool_event_survives_null_message_and_content():
    """A null `message`/`content` defeats dict.get defaults, and the raised
    AttributeError would drop the timing for every tool after it."""
    null_events = [
        {"type": "user", "message": None},
        {"type": "assistant", "message": None},
        {"type": "assistant", "message": {"content": None}},
        {"type": "user", "message": {"content": "plain text"}},
    ]
    events_at = [(event, 1050.0) for event in null_events]
    assert _stamp(
        [(TOOL_USE_EVENT, 1000.0)] + events_at + [(TOOL_RESULT_EVENT, 1250.0)]
    ) == {"toolu_01": 250}


@patch('generators.models.claude_code.os.makedirs')
@patch('generators.models.claude_code.open', create=True)
def test_parse_stream_json_accumulates_tool_durations(
        mock_open, mock_makedirs, monkeypatch):
    """durationMs was initialized and never accumulated, so tool_call_latency
    scored 0 for every scenario."""
    monkeypatch.setenv("HOME", "/fake/real_home")
    mock_open.return_value.__enter__.return_value.read.return_value = '{}'

    generator = ClaudeCodeGenerator({"model": "claude-opus-4-6"})
    stream = "\n".join(
        json.dumps(e)
        for e in (TOOL_USE_EVENT, TOOL_RESULT_EVENT, RESULT_EVENT))

    parsed = json.loads(generator._parse_stream_json(
        stream, tool_durations={"toolu_01": 250}))

    tools = parsed["stats"]["tools"]
    assert tools["totalDurationMs"] == 250
    assert tools["byName"]["cloud-sql__list_instances"]["durationMs"] == 250


def test_extract_skills_strips_plugin_namespace():
    """The `Skill` tool names plugin skills `<plugin>:<skill>`, which matches no
    skill directory, so both skills scorers graded an activated skill as 0."""
    generator = object.__new__(ClaudeCodeGenerator)
    stdout = json.dumps({"stats": {"tools": {"byName": {"Skill": {"parameters": [
        {"skill": "cloud-sql-postgresql:cloud-sql-postgres-admin"}]}}}}})

    with patch.object(ClaudeCodeGenerator, '_get_installed_skills', return_value=set()):
        assert generator.extract_skills(stdout) == ["cloud-sql-postgres-admin"]


def test_translate_mcp_config_forwards_oauth_scopes():
    generator = object.__new__(ClaudeCodeGenerator)
    generator.env = {}

    mcp_config = {
        "httpUrl": "https://test-dfareporting.sandbox.googleapis.com/mcp",
        "authProviderType": "google_credentials",
        "oauth": {
            "scopes": [
                "https://www.googleapis.com/auth/cloud-platform",
                "https://www.googleapis.com/auth/dfareporting",
            ]
        },
        "headers": {
            "X-Goog-User-Project": "my-project"
        }
    }

    with patch.object(generator, '_fetch_gcloud_access_token', return_value="fake_token") as mock_fetch:
        translated = generator._translate_mcp_config("dfareporting", mcp_config)

        assert "oauth" not in translated
        assert "authProviderType" not in translated
        assert translated["type"] == "http"
        assert translated["url"] == "https://test-dfareporting.sandbox.googleapis.com/mcp"
        assert translated["headers"]["Authorization"] == "Bearer fake_token"
        assert translated["headers"]["X-Goog-User-Project"] == "my-project"
        assert '--scopes="https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/dfareporting"' in translated["headersHelper"]

        mock_fetch.assert_called_once_with(scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/dfareporting",
        ])


def test_fetch_gcloud_access_token_passes_scopes():
    generator = object.__new__(ClaudeCodeGenerator)
    generator.env = {}

    scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/dfareporting",
    ]

    with patch('generators.models.claude_code.subprocess.run') as mock_run:
        mock_proc = MagicMock()
        mock_proc.stdout = "scoped_token_xyz\n"
        mock_run.return_value = mock_proc

        token = generator._fetch_gcloud_access_token(scopes=scopes)

        assert token == "scoped_token_xyz"
        first_call_cmd = mock_run.call_args_list[0][0][0]
        assert first_call_cmd == [
            "gcloud", "auth", "application-default", "print-access-token",
            "--scopes=https://www.googleapis.com/auth/cloud-platform,https://www.googleapis.com/auth/dfareporting",
        ]


def test_headers_helper_rejects_unsafe_scopes():
    with pytest.raises(ValueError, match="Invalid or unsafe OAuth scope"):
        ClaudeCodeGenerator._google_credentials_headers_helper(
            scopes=['https://example.com"; rm -rf /; "']
        )


def test_fetch_gcloud_access_token_rejects_unsafe_scopes():
    generator = object.__new__(ClaudeCodeGenerator)
    generator.env = {}
    with pytest.raises(ValueError, match="Invalid or unsafe OAuth scope"):
        generator._fetch_gcloud_access_token(
            scopes=['$(whoami)']
        )


def _vertex_generator(tmp_path, monkeypatch, server_mode):
    """Builds a Vertex ClaudeCodeGenerator whose real HOME holds a gcloud
    config with cached credentials, in local or eval-server mode."""
    real_gcloud = tmp_path / "home" / ".config" / "gcloud"
    real_gcloud.mkdir(parents=True)
    (real_gcloud / "access_tokens.db").write_text("cached-token")
    (real_gcloud / "application_default_credentials.json").write_text("{}")
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.delenv("GOOGLE_APPLICATION_CREDENTIALS", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "generators.models.claude_code.SESSIONS_ROOT", str(tmp_path / "sessions"))
    argv0 = "/evalbench/evalbench/eval_server.py" if server_mode else "pytest"
    monkeypatch.setattr(sys, "argv", [argv0])
    return ClaudeCodeGenerator(
        {"model": "claude-opus-4-6", "use_vertex": True, "session_id": "s1"})


def test_eval_server_does_not_copy_host_gcloud_config(
    tmp_path, monkeypatch
):
    """On the eval server the host's gcloud config is not copied into the
    session sandbox and no ADC file is set. Claude and gcloud use the
    metadata server."""
    generator = _vertex_generator(tmp_path, monkeypatch, server_mode=True)

    fake_gcloud = os.path.join(generator.fake_home, ".config", "gcloud")
    assert generator.fake_home.startswith(str(tmp_path / "sessions"))
    assert not os.path.exists(os.path.join(fake_gcloud, "access_tokens.db"))
    assert "GOOGLE_APPLICATION_CREDENTIALS" not in generator.env
    assert generator.env["CLOUDSDK_CONFIG"] == fake_gcloud


def test_local_run_copies_gcloud_credentials_into_the_sandbox(
    tmp_path, monkeypatch
):
    """Locally the developer's gcloud login is copied into the sandbox, so
    skill scripts can use it."""
    generator = _vertex_generator(tmp_path, monkeypatch, server_mode=False)

    fake_gcloud = os.path.join(generator.fake_home, ".config", "gcloud")
    assert os.path.exists(os.path.join(fake_gcloud, "access_tokens.db"))
    assert generator.env["GOOGLE_APPLICATION_CREDENTIALS"] == os.path.join(
        fake_gcloud, "application_default_credentials.json")


def _account(uid=1001, gid=1001):
    return pwd.struct_passwd(
        ("claudeuser", "x", uid, gid, "", "/home/claudeuser", "/bin/bash"))


def _as_root(account):
    """Makes the generator see a root EvalBench and the given passwd entry."""
    lookup = {"return_value": account} if account else {"side_effect": KeyError("claudeuser")}
    return (
        patch('generators.models.claude_code.os.geteuid', return_value=0),
        patch('generators.models.claude_code.pwd.getpwnam', **lookup),
        patch('generators.models.claude_code.os.getgrouplist', return_value=[1001]),
    )


def test_claude_code_keeps_the_account_when_evalbench_is_not_root():
    generator = object.__new__(ClaudeCodeGenerator)
    generator.fake_home = os.getcwd()

    with patch('generators.models.claude_code.os.geteuid', return_value=1000), \
            patch('generators.models.claude_code._chown_tree') as mock_chown, \
            patch('generators.models.claude_code.subprocess.run') as mock_run:
        generator._execute_cli_command(["claude", "-p", "hi"], env={})

    mock_chown.assert_not_called()
    for name in ("user", "group", "extra_groups"):
        assert name not in mock_run.call_args.kwargs


def test_claude_code_runs_as_the_non_root_account_when_evalbench_is_root(tmp_path):
    generator = object.__new__(ClaudeCodeGenerator)
    generator.fake_home = str(tmp_path)
    proc = MagicMock(stdout=[], stderr=[], returncode=0)

    root, getpwnam, groups = _as_root(_account())
    with root, getpwnam as mock_getpwnam, groups, \
            patch('generators.models.claude_code._chown_tree') as mock_chown, \
            patch('generators.models.claude_code.subprocess.Popen', return_value=proc) as mock_popen, \
            patch('generators.models.claude_code.subprocess.run') as mock_run:
        generator._execute_cli_streaming(["claude", "-p", "hi"], env={})
        generator._execute_cli_command(["claude", "plugins", "install", "x"], env={})

    mock_getpwnam.assert_called_with("claudeuser")
    for call in (mock_popen.call_args, mock_run.call_args):
        assert call.kwargs["user"] == 1001
        assert call.kwargs["group"] == 1001
        assert call.kwargs["extra_groups"] == [1001]
        assert call.kwargs["cwd"] == str(tmp_path)
    mock_chown.assert_called_with(str(tmp_path), 1001, 1001)


def test_work_dir_outside_the_sandbox_home_keeps_its_owner(tmp_path, caplog):
    generator = object.__new__(ClaudeCodeGenerator)
    generator.fake_home = str(tmp_path / "home")
    work_dir = tmp_path / "dataset_work_dir"
    os.makedirs(generator.fake_home)
    work_dir.mkdir()

    root, getpwnam, groups = _as_root(_account())
    with root, getpwnam, groups, \
            patch('generators.models.claude_code._chown_tree') as mock_chown:
        generator._non_root_kwargs(str(work_dir))
        generator._non_root_kwargs(str(work_dir))

    assert [call.args[0] for call in mock_chown.call_args_list] == [generator.fake_home] * 2
    assert caplog.text.count("keeps its owner") == 1


@pytest.mark.parametrize("account", [None, _account(uid=0, gid=0)], ids=["no_account", "account_is_root"])
def test_root_evalbench_without_a_non_root_account_stops(tmp_path, monkeypatch, account):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "real_home"))

    root, getpwnam, groups = _as_root(account)
    with root, getpwnam, groups:
        with pytest.raises(RuntimeError, match="Claude Code must not"):
            ClaudeCodeGenerator({})


def test_generator_does_not_set_the_root_bypass(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path / "real_home"))

    generator = ClaudeCodeGenerator({})

    assert "IS_SANDBOX" not in generator.env


def test_chown_tree_does_not_leave_the_tree(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret").write_text("x")
    tree = tmp_path / "tree"
    (tree / "sub").mkdir(parents=True)
    (tree / "sub" / "file").write_text("x")
    (tree / "link_to_file").symlink_to(outside / "secret")
    (tree / "link_to_dir").symlink_to(outside, target_is_directory=True)
    os.link(outside / "secret", tree / "hard_link")

    by_name, by_fd = [], []

    def fake_chown(path, uid, gid, *, dir_fd=None, follow_symlinks=True):
        if isinstance(path, int):
            by_fd.append(path)
        else:
            by_name.append((path, dir_fd is not None, follow_symlinks))

    # Another account, so that no entry counts as "already has this owner".
    with patch('generators.models.claude_code.os.chown', side_effect=fake_chown):
        _chown_tree(str(tree), os.getuid() + 1, os.getgid() + 1)

    assert len(by_fd) == 2  # tree and tree/sub
    assert sorted(name for name, _, _ in by_name) == ["file", "link_to_dir", "link_to_file", "sub"]
    assert all(has_dir_fd and not follow for _, has_dir_fd, follow in by_name)


def test_chown_tree_skips_a_link_at_the_root(tmp_path):
    target = tmp_path / "target"
    target.mkdir()
    (target / "file").write_text("x")
    link = tmp_path / "link"
    link.symlink_to(target, target_is_directory=True)

    with patch('generators.models.claude_code.os.chown') as mock_chown:
        _chown_tree(str(link), os.getuid() + 1, os.getgid() + 1)

    mock_chown.assert_not_called()
