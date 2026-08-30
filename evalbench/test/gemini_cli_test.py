import os
import signal
import sys
from unittest.mock import MagicMock, patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.gemini_cli import GeminiCliGenerator


@patch('generators.models.gemini_cli.subprocess.run')
@patch('generators.models.gemini_cli.os.path.exists')
@patch('generators.models.gemini_cli.shutil.copytree')
@patch('generators.models.gemini_cli.shutil.rmtree')
def test_setup_single_skill_string(
    mock_rmtree,
    mock_copytree,
    mock_exists,
    mock_run,
    monkeypatch,
):
    real_home = "/fake/real_home"
    real_skill_path = os.path.join(real_home, ".gemini", "skills", "my-single-skill")

    mock_exists.side_effect = lambda path: path == real_skill_path
    mock_run.return_value = MagicMock(returncode=0, stdout="fake-token")

    config = {"setup": {"skills": ["my-single-skill"]}}

    monkeypatch.setenv("HOME", real_home)
    with (
        patch('generators.models.gemini_cli.os.makedirs'),
        patch('generators.models.gemini_cli.open', create=True),
    ):
        GeminiCliGenerator(config)

    expected_fake_home = os.path.abspath(os.path.join(".venv", "fake_home"))
    expected_fake_skill_path = os.path.join(
        expected_fake_home, ".gemini", "skills", "my-single-skill"
    )
    mock_copytree.assert_called_once_with(real_skill_path, expected_fake_skill_path)


def test_skill_content_preserved(tmp_path, monkeypatch):
    real_home = tmp_path / "real_home"
    real_home.mkdir()

    skill_name = "my-content-skill"
    real_skill_dir = real_home / ".gemini" / "skills" / skill_name
    real_skill_dir.mkdir(parents=True)

    skill_file = real_skill_dir / "secret.txt"
    expected_content = "hello world"
    skill_file.write_text(expected_content)

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HOME", str(real_home))

    generator = GeminiCliGenerator({})

    generator._setup_skills([skill_name])

    expected_fake_skill_file = (
        tmp_path / ".venv" / "fake_home" / ".gemini" / "skills" / skill_name / "secret.txt"
    )
    assert expected_fake_skill_file.exists(), (
        f"Copied skill file not found at {expected_fake_skill_file}"
    )
    assert expected_fake_skill_file.read_text() == expected_content


@patch('generators.models.gemini_cli.subprocess.run')
@patch('generators.models.gemini_cli.os.path.exists')
@patch('generators.models.gemini_cli.shutil.copytree')
@patch('generators.models.gemini_cli.shutil.rmtree')
def test_setup_multiple_skills_string(
    mock_rmtree,
    mock_copytree,
    mock_exists,
    mock_run,
    monkeypatch,
):
    real_home = "/fake/real_home"
    skill_a_path = os.path.join(real_home, ".gemini", "skills", "skill-A")
    skill_b_path = os.path.join(real_home, ".gemini", "skills", "skill-B")

    mock_exists.side_effect = lambda path: path in (skill_a_path, skill_b_path)
    mock_run.return_value = MagicMock(returncode=0, stdout="fake-token")

    config = {"setup": {"skills": ["skill-A", "skill-B"]}}

    monkeypatch.setenv("HOME", real_home)
    with (
        patch('generators.models.gemini_cli.os.makedirs'),
        patch('generators.models.gemini_cli.open', create=True),
    ):
        GeminiCliGenerator(config)

    expected_fake_home = os.path.abspath(os.path.join(".venv", "fake_home"))
    expected_fake_a = os.path.join(expected_fake_home, ".gemini", "skills", "skill-A")
    expected_fake_b = os.path.join(expected_fake_home, ".gemini", "skills", "skill-B")

    assert mock_copytree.call_count == 2
    mock_copytree.assert_any_call(skill_a_path, expected_fake_a)
    mock_copytree.assert_any_call(skill_b_path, expected_fake_b)


@patch('generators.models.gemini_cli.subprocess.run')
@patch('generators.models.gemini_cli.os.path.exists')
def test_setup_skill_dict_link(
    mock_exists,
    mock_run,
    monkeypatch,
):
    real_home = "/fake/real_home"
    mock_exists.return_value = False
    mock_run.return_value = MagicMock(returncode=0, stdout="success")

    config = {
        "gemini_cli_version": "gemini-cli@0.36.0",
        "setup": {
            "skills": [
                {
                    "action": "link",
                    "path": "/path/to/my-skill"
                }
            ]
        }
    }

    monkeypatch.setenv("HOME", real_home)
    with (
        patch('generators.models.gemini_cli.os.makedirs'),
        patch('generators.models.gemini_cli.open', create=True),
    ):
        GeminiCliGenerator(config)

    assert mock_run.call_count == 3
    calls = [call[0][0] for call in mock_run.call_args_list]

    expected_cmd = [
        "npm",
        "exec",
        "--yes",
        "gemini-cli@0.36.0",
        "--",
        "skills",
        "link",
        "/path/to/my-skill",
        "--consent",
    ]
    assert expected_cmd in calls


@patch('generators.models.gemini_cli.subprocess.run')
@patch('generators.models.gemini_cli.os.path.exists')
def test_setup_skill_dict_install_by_path(
    mock_exists,
    mock_run,
    monkeypatch,
):
    real_home = "/fake/real_home"
    mock_exists.return_value = False
    mock_run.return_value = MagicMock(returncode=0, stdout="success")

    config = {
        "gemini_cli_version": "gemini-cli@0.36.0",
        "setup": {
            "skills": [
                {
                    "action": "install",
                    "path": "/path/to/my-skill"
                }
            ]
        }
    }

    monkeypatch.setenv("HOME", real_home)
    with (
        patch('generators.models.gemini_cli.os.makedirs'),
        patch('generators.models.gemini_cli.open', create=True),
    ):
        GeminiCliGenerator(config)

    assert mock_run.call_count == 3
    calls = [call[0][0] for call in mock_run.call_args_list]

    expected_cmd = [
        "npm",
        "exec",
        "--yes",
        "gemini-cli@0.36.0",
        "--",
        "skills",
        "install",
        "/path/to/my-skill",
        "--consent",
    ]
    assert expected_cmd in calls


@patch('generators.models.gemini_cli.subprocess.run')
@patch('generators.models.gemini_cli.os.path.exists')
def test_setup_skill_dict_install_by_name(
    mock_exists,
    mock_run,
    monkeypatch,
):
    real_home = "/fake/real_home"
    mock_exists.return_value = False
    mock_run.return_value = MagicMock(returncode=0, stdout="success")

    config = {
        "gemini_cli_version": "gemini-cli@0.36.0",
        "setup": {
            "skills": [
                {
                    "action": "install",
                    "name": "my-skill-package"
                }
            ]
        }
    }

    monkeypatch.setenv("HOME", real_home)
    with (
        patch('generators.models.gemini_cli.os.makedirs'),
        patch('generators.models.gemini_cli.open', create=True),
    ):
        GeminiCliGenerator(config)

    assert mock_run.call_count == 3
    calls = [call[0][0] for call in mock_run.call_args_list]

    expected_cmd = [
        "npm",
        "exec",
        "--yes",
        "gemini-cli@0.36.0",
        "--",
        "skills",
        "install",
        "my-skill-package",
        "--consent",
    ]
    assert expected_cmd in calls


@patch('generators.models.gemini_cli.subprocess.run')
@patch('generators.models.gemini_cli.os.path.exists')
def test_setup_skill_dict_enable(
    mock_exists,
    mock_run,
    monkeypatch,
):
    real_home = "/fake/real_home"
    mock_exists.return_value = False
    mock_run.return_value = MagicMock(returncode=0, stdout="success")

    config = {
        "gemini_cli_version": "gemini-cli@0.36.0",
        "setup": {
            "skills": [
                {
                    "action": "enable",
                    "name": "my-skill"
                }
            ]
        }
    }

    monkeypatch.setenv("HOME", real_home)
    with (
        patch('generators.models.gemini_cli.os.makedirs'),
        patch('generators.models.gemini_cli.open', create=True),
    ):
        GeminiCliGenerator(config)

    assert mock_run.call_count == 3
    calls = [call[0][0] for call in mock_run.call_args_list]

    expected_cmd = [
        "npm",
        "exec",
        "--yes",
        "gemini-cli@0.36.0",
        "--",
        "skills",
        "enable",
        "my-skill",
    ]
    assert expected_cmd in calls


@patch('generators.models.gemini_cli.subprocess.run')
@patch('generators.models.gemini_cli.os.path.exists')
def test_setup_skill_dict_disable(
    mock_exists,
    mock_run,
    monkeypatch,
):
    real_home = "/fake/real_home"
    mock_exists.return_value = False
    mock_run.return_value = MagicMock(returncode=0, stdout="success")

    config = {
        "gemini_cli_version": "gemini-cli@0.36.0",
        "setup": {
            "skills": [
                {
                    "action": "disable",
                    "name": "my-skill"
                }
            ]
        }
    }

    monkeypatch.setenv("HOME", real_home)
    with (
        patch('generators.models.gemini_cli.os.makedirs'),
        patch('generators.models.gemini_cli.open', create=True),
    ):
        GeminiCliGenerator(config)

    assert mock_run.call_count == 3
    calls = [call[0][0] for call in mock_run.call_args_list]

    expected_cmd = [
        "npm",
        "exec",
        "--yes",
        "gemini-cli@0.36.0",
        "--",
        "skills",
        "disable",
        "my-skill",
    ]
    assert expected_cmd in calls


def test_execute_cli_command_timeout_preserves_stdout_and_kills_pg(monkeypatch):
    monkeypatch.setenv("HOME", "/fake/real_home")

    with (
        patch('generators.models.gemini_cli.os.makedirs'),
        patch('generators.models.gemini_cli.os.path.exists', return_value=False),
        patch('generators.models.gemini_cli.open', create=True),
    ):
        generator = GeminiCliGenerator({})

    mock_proc = MagicMock()
    mock_proc.pid = 12345
    stream_output = '{"type":"init","session_id":"s1"}\n{"type":"message","role":"assistant","content":"hello"}\n'
    mock_proc.communicate.return_value = (stream_output, "")

    with (
        patch('generators.models.gemini_cli.subprocess.Popen', return_value=mock_proc) as mock_popen,
        patch('generators.models.agent_cli.os.killpg') as mock_killpg,
        patch('generators.models.agent_cli.os.getpgid', return_value=12345),
        patch('generators.models.agent_cli.threading.Timer') as mock_timer_cls,
    ):
        def fake_timer(interval, function):
            function()
            return MagicMock()

        mock_timer_cls.side_effect = fake_timer

        cli_cmd = generator.create_command("gemini", "prompt", timeout=1)
        res = generator._run_gemini_cli(cli_cmd)

        mock_popen.assert_called_once()
        assert mock_popen.call_args.kwargs.get("start_new_session") is True
        mock_killpg.assert_called_once_with(12345, signal.SIGKILL)
        assert res.returncode == 124
        assert "Error: Command timed out after 1 seconds." in res.stderr
        assert "hello" in res.stdout
