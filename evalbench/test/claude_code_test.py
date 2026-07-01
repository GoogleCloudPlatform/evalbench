import os
import sys
from unittest.mock import MagicMock, patch, ANY

# Add parent directory to path so we can import generators
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.claude_code import ClaudeCodeGenerator


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
