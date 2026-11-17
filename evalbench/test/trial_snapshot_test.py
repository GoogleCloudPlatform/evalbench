import os
import shutil
import stat
import subprocess
import sys
from unittest.mock import patch

import pytest

from evaluator.trialsnapshot import TrialSnapshot, check_resettable


@pytest.fixture
def home(tmp_path):
    """A sandbox home with a setup file, a credential, and a cache."""
    home = tmp_path / "fake_home"
    (home / ".gemini").mkdir(parents=True)
    (home / ".gemini" / "settings.json").write_text("setup")
    credential = home / ".config" / "adc.json"
    credential.parent.mkdir()
    credential.write_text("secret")
    credential.chmod(0o600)
    (home / ".npm").mkdir()
    (home / ".npm" / "package.tgz").write_text("cached")
    return home


def _files(directory):
    """Returns the relative path and the content of every file in a directory."""
    files = {}
    for root, _, names in os.walk(directory):
        for name in names:
            path = os.path.join(root, name)
            with open(path) as f:
                files[os.path.relpath(path, directory)] = f.read()
    return files


def _snapshot_dirs(parent):
    """Returns the directories in `parent` that hold a snapshot."""
    return [parent / name for name in os.listdir(parent) if "_trial_base_" in name]


def test_restore_removes_what_a_trial_added(home):
    before = _files(home)
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    (home / "result.sql").write_text("select 1")
    (home / "project" / "models").mkdir(parents=True)
    (home / "project" / "models" / "a.sql").write_text("select 2")
    snapshot.restore()

    assert _files(home) == before


def test_restore_brings_back_changed_and_deleted_files(home):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    (home / ".gemini" / "settings.json").write_text("changed by a trial")
    (home / ".config" / "adc.json").unlink()
    snapshot.restore()

    assert (home / ".gemini" / "settings.json").read_text() == "setup"
    credential = home / ".config" / "adc.json"
    assert credential.read_text() == "secret"
    assert stat.S_IMODE(credential.stat().st_mode) == 0o600


def test_caches_are_not_copied_and_not_reset(home, tmp_path):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()
    (snapshot_dir,) = _snapshot_dirs(tmp_path)
    copied = _files(snapshot_dir)

    (home / ".npm" / "new.tgz").write_text("downloaded by a trial")
    snapshot.restore()

    assert not any("package.tgz" in path for path in copied)
    assert (home / ".npm" / "new.tgz").exists()
    assert (home / ".npm" / "package.tgz").read_text() == "cached"


def test_work_dir_is_restored(home, tmp_path):
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    (work_dir / "starter.sql").write_text("select 1")
    snapshot = TrialSnapshot(str(home), [str(work_dir)])
    snapshot.save()

    (work_dir / "starter.sql").write_text("edited")
    (work_dir / "output.sql").write_text("select 2")
    snapshot.restore()

    assert _files(work_dir) == {"starter.sql": "select 1"}


def test_work_dir_that_did_not_exist_is_removed(home, tmp_path):
    work_dir = tmp_path / "workspace"
    snapshot = TrialSnapshot(str(home), [str(work_dir)])
    snapshot.save()

    work_dir.mkdir()
    (work_dir / "output.sql").write_text("select 2")
    snapshot.restore()

    assert not work_dir.exists()


def test_links_stay_links_and_their_targets_are_not_touched(home, tmp_path):
    outside = tmp_path / "real_skill"
    outside.mkdir()
    (outside / "SKILL.md").write_text("skill")
    (home / "skill").symlink_to(outside)
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    # A trial adds a link of its own.
    (home / "trial_link").symlink_to(outside)
    snapshot.restore()

    assert (home / "skill").is_symlink()
    assert os.readlink(home / "skill") == str(outside)
    assert not (home / "trial_link").exists()
    assert _files(outside) == {"SKILL.md": "skill"}


def test_pipes_are_not_copied(home):
    os.mkfifo(home / "agent.pipe")
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    (home / "agent.pipe").unlink()
    snapshot.restore()

    assert not (home / "agent.pipe").exists()
    assert (home / ".gemini" / "settings.json").read_text() == "setup"


def test_restore_removes_read_only_directories(home):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    cache = home / "go" / "pkg"
    cache.mkdir(parents=True)
    (cache / "module.go").write_text("package a")
    cache.chmod(0o555)
    snapshot.restore()

    assert not (home / "go").exists()


def test_snapshot_is_private_and_next_to_the_home(home, tmp_path):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    (snapshot_dir,) = _snapshot_dirs(tmp_path)
    assert snapshot_dir.name.startswith("fake_home_trial_base_")
    assert stat.S_IMODE(snapshot_dir.stat().st_mode) == 0o700

    snapshot.remove()
    assert _snapshot_dirs(tmp_path) == []


def _ended_process_id():
    """Returns the ID of a process that ended."""
    process = subprocess.Popen([sys.executable, "-c", "pass"])
    process.wait()
    return process.pid


def test_save_deletes_the_snapshot_of_a_killed_job(home, tmp_path):
    leftover = tmp_path / f"fake_home_trial_base_{_ended_process_id()}_abc"
    leftover.mkdir()
    (leftover / "adc.json").write_text("secret")
    # The snapshot of another sandbox home in the same directory.
    other = tmp_path / f"fake_home_claude_trial_base_{_ended_process_id()}_abc"
    other.mkdir()

    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    assert not leftover.exists()
    assert other.exists()
    assert len(_snapshot_dirs(tmp_path)) == 2  # The new one and `other`.


def test_save_keeps_the_snapshot_of_a_job_that_still_runs(
        home, tmp_path, caplog):
    # This test process stands in for the other job.
    running = tmp_path / f"fake_home_trial_base_{os.getpid()}_abc"
    running.mkdir()
    (running / "adc.json").write_text("secret")

    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    assert (running / "adc.json").exists()
    assert "The reset of one job deletes the files of the other" in caplog.text


def test_snapshot_name_has_the_process_id(home, tmp_path):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    (snapshot_dir,) = _snapshot_dirs(tmp_path)
    assert snapshot_dir.name.startswith(
        f"fake_home_trial_base_{os.getpid()}_")


def test_kept_copies_survive_remove_and_the_next_job(home, tmp_path):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()

    kept = snapshot.keep()
    snapshot.remove()
    TrialSnapshot(str(home), []).save()

    assert os.path.basename(kept).startswith("fake_home_failed_reset_")
    assert _files(os.path.join(kept, "0"))[".gemini/settings.json"] == "setup"


def test_keep_returns_none_if_the_copies_are_gone(home, tmp_path):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()
    (snapshot_dir,) = _snapshot_dirs(tmp_path)
    shutil.rmtree(snapshot_dir)

    assert snapshot.keep() is None


def test_restore_after_remove_changes_nothing(home):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()
    snapshot.remove()

    (home / "result.sql").write_text("select 1")
    snapshot.restore()

    assert (home / "result.sql").exists()
    assert (home / ".gemini" / "settings.json").read_text() == "setup"


def test_restore_without_the_copy_raises_and_deletes_nothing(home, tmp_path):
    snapshot = TrialSnapshot(str(home), [])
    snapshot.save()
    (snapshot_dir,) = _snapshot_dirs(tmp_path)
    shutil.rmtree(snapshot_dir)

    (home / "result.sql").write_text("select 1")
    with pytest.raises(FileNotFoundError, match="Nothing was reset"):
        snapshot.restore()

    assert (home / "result.sql").exists()
    assert (home / ".gemini" / "settings.json").read_text() == "setup"


def test_failed_save_leaves_no_snapshot(home, tmp_path):
    snapshot = TrialSnapshot(str(home), [])
    with patch("evaluator.trialsnapshot.shutil.copy2", side_effect=OSError("disk full")):
        with pytest.raises(OSError, match="disk full"):
            snapshot.save()

    assert _snapshot_dirs(tmp_path) == []


def test_work_dirs_are_reset_without_a_sandbox_home(tmp_path):
    work_dir = tmp_path / "workspace"
    work_dir.mkdir()
    snapshot = TrialSnapshot(None, [str(work_dir)])
    snapshot.save()

    (work_dir / "output.sql").write_text("select 2")
    snapshot.restore()
    snapshot.remove()

    assert os.listdir(work_dir) == []


def test_nothing_to_save_makes_no_snapshot(tmp_path):
    with patch("evaluator.trialsnapshot.tempfile.mkdtemp") as mock_mkdtemp:
        snapshot = TrialSnapshot(None, [])
        snapshot.save()
        snapshot.restore()
        snapshot.remove()

    mock_mkdtemp.assert_not_called()


def test_check_resettable(tmp_path):
    dataset_dir = tmp_path / "dataset"
    (dataset_dir / "workspaces" / "dbt").mkdir(parents=True)
    protected = [str(dataset_dir)]

    check_resettable("a", str(dataset_dir / "workspaces" / "dbt"), protected)
    check_resettable("a", str(tmp_path / "elsewhere"), protected)
    for unsafe in (dataset_dir, tmp_path, dataset_dir / "workspaces" / ".."):
        with pytest.raises(
            ValueError, match="of scenario a cannot be reset between trials"
        ):
            check_resettable("a", str(unsafe), protected)
