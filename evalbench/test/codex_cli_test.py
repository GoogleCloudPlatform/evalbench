import errno
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


@patch('generators.models.codex_cli.subprocess.Popen')
def test_execute_cli_command_timeout_wall_clock(mock_popen, monkeypatch):
    monkeypatch.setenv("HOME", "/fake/real_home")

    with (
        patch('generators.models.codex_cli.os.makedirs'),
        patch('generators.models.codex_cli.open', create=True),
    ):
        generator = CodexCliGenerator({"model": "gpt-4"})

    r, w = os.pipe()
    stdout_file = os.fdopen(r, "r")

    mock_proc = MagicMock()
    mock_proc.stdout = stdout_file
    mock_proc.stderr = []
    mock_proc.returncode = None

    def mock_kill():
        try:
            os.close(w)
        except OSError as e:
            # Ignore EBADF if the write pipe descriptor was already closed
            if e.errno != errno.EBADF:
                raise

    mock_proc.kill.side_effect = mock_kill
    mock_popen.return_value = mock_proc

    import time
    start = time.monotonic()
    res, _ = generator._execute_cli_command(["codex"], timeout=0.2)
    elapsed = time.monotonic() - start

    assert res.returncode == 124
    assert "timed out after 0.2 seconds" in res.stderr
    mock_proc.kill.assert_called_once()
    assert elapsed < 0.6


def test_create_command_passes_timeout():
    with (
        patch('generators.models.codex_cli.os.makedirs'),
        patch('generators.models.codex_cli.open', create=True),
    ):
        generator = CodexCliGenerator({"model": "gpt-4"})

    cli_cmd = generator.create_command("codex", "do something", timeout=120)
    assert cli_cmd.timeout == 120

    with patch.object(generator, '_execute_cli_command', return_value=(MagicMock(stdout="", stderr="", returncode=0), {})) as mock_exec:
        generator._run_codex_cli(cli_cmd)
        mock_exec.assert_called_once()
        assert mock_exec.call_args.kwargs.get("timeout") == 120


@patch('generators.models.codex_cli.subprocess.Popen')
def test_execute_cli_command_timeout_when_stdout_closes_before_proc_exit(mock_popen, monkeypatch):
    monkeypatch.setenv("HOME", "/fake/real_home")

    with (
        patch('generators.models.codex_cli.os.makedirs'),
        patch('generators.models.codex_cli.open', create=True),
    ):
        generator = CodexCliGenerator({"model": "gpt-4"})

    mock_proc = MagicMock()
    mock_proc.stdout = []  # EOF immediately on stdout
    mock_proc.stderr = []
    mock_proc.returncode = None
    mock_proc.pid = 99999

    import threading
    wait_event = threading.Event()

    def mock_wait():
        wait_event.wait(timeout=2.0)
        return 0

    def mock_kill():
        wait_event.set()

    mock_proc.wait.side_effect = mock_wait
    mock_proc.kill.side_effect = mock_kill
    mock_popen.return_value = mock_proc

    import time
    start = time.monotonic()
    res, _ = generator._execute_cli_command(["codex"], timeout=0.2)
    elapsed = time.monotonic() - start

    assert res.returncode == 124
    assert "timed out after 0.2 seconds" in res.stderr
    mock_proc.kill.assert_called_once()
    assert elapsed < 0.6
