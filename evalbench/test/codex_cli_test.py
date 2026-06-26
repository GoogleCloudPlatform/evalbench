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
