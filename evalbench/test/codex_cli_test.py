import os
import sys
from unittest.mock import MagicMock, patch

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.codex_cli import CodexCliGenerator


@patch('generators.models.codex_cli.subprocess.Popen')
def test_execute_cli_command_passes_cwd(mock_popen, monkeypatch):
    monkeypatch.setenv("HOME", "/fake/real_home")

    with (
        patch('generators.models.codex_cli.os.makedirs'),
        patch('generators.models.codex_cli.open', create=True),
    ):
        generator = CodexCliGenerator({"model": "gpt-4"})

    mock_proc = MagicMock()
    mock_proc.stdout = []
    mock_proc.stderr = []
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    cli_cmd = generator.create_command("codex", "do something", cwd="/some/custom/cwd")
    assert cli_cmd.cwd == "/some/custom/cwd"

    generator.safe_generate(cli_cmd)

    mock_popen.assert_called_once()
    kwargs = mock_popen.call_args.kwargs
    assert kwargs.get("cwd") == "/some/custom/cwd"


@patch('generators.models.codex_cli.subprocess.Popen')
def test_execute_cli_command_default_cwd(mock_popen, monkeypatch):
    monkeypatch.setenv("HOME", "/fake/real_home")

    with (
        patch('generators.models.codex_cli.os.makedirs'),
        patch('generators.models.codex_cli.open', create=True),
    ):
        generator = CodexCliGenerator({"model": "gpt-4"})

    mock_proc = MagicMock()
    mock_proc.stdout = []
    mock_proc.stderr = []
    mock_proc.returncode = 0
    mock_popen.return_value = mock_proc

    cli_cmd = generator.create_command("codex", "do something")
    assert cli_cmd.cwd is None

    generator.safe_generate(cli_cmd)

    mock_popen.assert_called_once()
    kwargs = mock_popen.call_args.kwargs
    assert kwargs.get("cwd") == generator.fake_home


@patch('generators.models.codex_cli.subprocess.run')
def test_register_codex_plugin_uses_merged_env(mock_run, monkeypatch):
    monkeypatch.setenv("HOME", "/fake/real_home")
    monkeypatch.setenv("PARENT_VAR", "parent_value")

    with (
        patch('generators.models.codex_cli.os.makedirs'),
        patch('generators.models.codex_cli.open', create=True),
    ):
        generator = CodexCliGenerator({"model": "gpt-4", "env": {"GENERATOR_VAR": "gen_value"}})

    with (
        patch('generators.models.codex_cli.os.path.exists', return_value=False),
        patch('generators.models.codex_cli.open', create=True),
        patch('generators.models.codex_cli.json.dump'),
    ):
        generator._register_codex_plugin("/fake/repo_dir", {"plugin_name": "my-plugin"})

    mock_run.assert_called_once()
    kwargs = mock_run.call_args.kwargs
    passed_env = kwargs.get("env")
    assert passed_env is not None
    assert passed_env.get("PARENT_VAR") == "parent_value"
    assert passed_env.get("GENERATOR_VAR") == "gen_value"
    assert passed_env.get("HOME") == generator.fake_home


def test_streaming_timeout_keeps_stderr_diagnostics():
    """A timeout discarded the drained stderr, losing the CLI's own reason for
    hanging just when it is needed."""
    generator = object.__new__(CodexCliGenerator)
    generator.fake_home = os.getcwd()

    result, _ = CodexCliGenerator._execute_cli_command(
        generator,
        # `exec` so the kill lands on sleep itself; a forked child would hold
        # the stderr pipe open and stall the reader thread's join.
        ["sh", "-c", "echo 'rate limit reached' >&2; exec sleep 30"],
        timeout_seconds=1,
    )

    assert result.returncode == 124
    assert "TimeoutError: Command timed out after 1 seconds" in result.stderr
    assert "rate limit reached" in result.stderr


def test_write_config_toml_escapes_plugin_id(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", "/fake/real_home")

    with (
        patch('generators.models.codex_cli.os.makedirs'),
        patch('generators.models.codex_cli.open', create=True),
    ):
        generator = CodexCliGenerator({"model": "gpt-4"})

    generator.enabled_plugins = {
        "dak@evalbench-local-marketplace": {"opt1": "val1"},
        "clean_plugin": {}
    }

    config_file = tmp_path / "config.toml"
    generator.config_path = str(config_file)

    generator._write_config_toml()

    content = config_file.read_text()
    assert '[plugins."dak@evalbench-local-marketplace"]' in content
    assert '[plugins.clean_plugin]' in content


def test_translate_mcp_config_forwards_oauth_scopes():
    generator = object.__new__(CodexCliGenerator)
    generator.env = {}
    generator._gcloud_mcp_scopes = []

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
        assert translated["url"] == "https://test-dfareporting.sandbox.googleapis.com/mcp"
        assert translated["bearer_token_env_var"] == "EVALBENCH_GCLOUD_MCP_TOKEN"
        assert generator.env["EVALBENCH_GCLOUD_MCP_TOKEN"] == "fake_token"
        assert translated["http_headers"]["X-Goog-User-Project"] == "my-project"
        assert generator._gcloud_mcp_scopes == [
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/dfareporting",
        ]

        mock_fetch.assert_called_once_with(scopes=[
            "https://www.googleapis.com/auth/cloud-platform",
            "https://www.googleapis.com/auth/dfareporting",
        ])


def test_fetch_gcloud_access_token_passes_scopes():
    generator = object.__new__(CodexCliGenerator)
    generator.env = {}

    scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/dfareporting",
    ]

    with patch('generators.models.codex_cli.subprocess.run') as mock_run:
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


def test_run_codex_cli_refreshes_token_with_recorded_scopes():
    generator = object.__new__(CodexCliGenerator)
    generator.env = {}
    generator._needs_gcloud_mcp_token = True
    generator._gcloud_mcp_scopes = [
        "https://www.googleapis.com/auth/cloud-platform",
        "https://www.googleapis.com/auth/dfareporting",
    ]
    generator.json_flag = "--json"
    generator.sandbox_mode = "danger-full-access"
    generator.approval_mode = "never"
    generator.model = None
    generator.profile = None

    cli_cmd = MagicMock()
    cli_cmd.cli = "codex"
    cli_cmd.resume = False
    cli_cmd.session_id = None
    cli_cmd.prompt = "test prompt"
    cli_cmd.env = {}
    cli_cmd.cwd = None

    mock_completed = MagicMock()
    mock_completed.stdout = ""
    mock_completed.stderr = ""

    with (
        patch.object(generator, '_fetch_gcloud_access_token', return_value="refreshed_token") as mock_fetch,
        patch.object(generator, '_execute_cli_command', return_value=(mock_completed, {})),
    ):
        generator._run_codex_cli(cli_cmd)
        mock_fetch.assert_called_once_with(scopes=generator._gcloud_mcp_scopes)
