"""Tests for the Dataform setup and teardown scripts in datasets/dea-tools.

The scripts run against a stub DataformWorkspaceManager, so the tests make
no cloud calls. The stub records the arguments that it receives.
"""

import json
import os
import pathlib
import shutil
import subprocess
import sys

import pytest

_SCRIPTS_DIR = (
    pathlib.Path(__file__).resolve().parents[2] / "datasets" / "dea-tools" / "scripts"
)

_STUB_MODULE = '''
import json
import os


def _record(method, *args, **kwargs):
    with open(os.environ["STUB_CALLS"], "a") as f:
        f.write(json.dumps([method, list(args), kwargs]) + "\\n")


class DataformWorkspaceManager:
    def __init__(self, project_id, location):
        _record("__init__", project_id, location)

    def setup_workspace(self, job_id, scenario_id="default", env_files_dir=None):
        _record("setup_workspace", job_id, scenario_id, env_files_dir=env_files_dir)
        return "projects/p/locations/l/repositories/r/workspaces/w"

    def teardown_workspace(self, workspace_uri):
        _record("teardown_workspace", workspace_uri)
'''

# A quote in the value closes the Python string literal if the script pastes
# the value into the Python source. Then the stub gets a different value.
_QUOTED = "w'); x = ('"

pytestmark = pytest.mark.skipif(
    shutil.which("bash") is None, reason="requires bash")


@pytest.fixture
def workdir(tmp_path):
    """Returns a working directory with the layout that the scripts expect."""
    util_dir = tmp_path / "work" / "evalbench" / "util"
    util_dir.mkdir(parents=True)
    # A regular package, so that it wins over any other "util" on sys.path.
    (util_dir / "__init__.py").write_text("")
    (util_dir / "dataform_workspace.py").write_text(_STUB_MODULE)
    venv_bin = tmp_path / "work" / ".venv" / "bin"
    venv_bin.mkdir(parents=True)
    (venv_bin / "python3").symlink_to(sys.executable)
    return tmp_path / "work"


def _run(script, workdir, session_dir, extra_env):
    calls = workdir / "calls.jsonl"
    env = {
        "PATH": os.environ.get("PATH", ""),
        "EVAL_GCP_PROJECT_ID": "proj",
        "EVAL_GCP_PROJECT_REGION": "us-central1",
        "STUB_CALLS": str(calls),
        **extra_env,
    }
    result = subprocess.run(
        ["bash", str(_SCRIPTS_DIR / script), str(session_dir)],
        cwd=workdir, env=env, capture_output=True, text=True, timeout=60,
    )
    assert result.returncode == 0, result.stderr
    return [json.loads(line) for line in calls.read_text().splitlines()]


def test_teardown_passes_state_file_content_as_data(workdir, tmp_path):
    session_dir = tmp_path / "session"
    session_dir.mkdir()
    state_file = session_dir / "target_workspace.txt"
    workspace_uri = "projects/p/locations/l/repositories/r/workspaces/" + _QUOTED
    state_file.write_text(workspace_uri)

    calls = _run("teardown_dataform.sh", workdir, session_dir, {})

    assert calls == [
        ["__init__", ["proj", "us-central1"], {}],
        ["teardown_workspace", [workspace_uri], {}],
    ]


def test_setup_passes_config_values_as_data(workdir, tmp_path):
    session_dir = tmp_path / "job-123"
    env_files_dir = "templates/" + _QUOTED

    calls = _run("setup_dataform.sh", workdir, session_dir, {
        "SCENARIO_ID": _QUOTED,
        "EVAL_DATAFORM_SETUP_ENV_FILES_DIR": env_files_dir,
    })

    assert calls == [
        ["__init__", ["proj", "us-central1"], {}],
        ["setup_workspace", ["job-123", _QUOTED],
         {"env_files_dir": env_files_dir}],
    ]
    assert (session_dir / "target_workspace.txt").read_text().strip() == (
        "projects/p/locations/l/repositories/r/workspaces/w")
