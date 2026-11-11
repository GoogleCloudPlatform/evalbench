"""Resets the directories that the trials of an agent eval share."""

import logging
import os
import shutil
import stat
import tempfile
from typing import FrozenSet, Iterable, List, NamedTuple, Optional

# Top-level entries of the sandbox home that the reset leaves alone: download
# caches and installed binaries. They are large, every trial needs them, and
# no scenario writes its results there.
HOME_SKIP = frozenset({".npm", ".cache", ".local"})


class _SavedDir(NamedTuple):
    path: str
    # The copy of the directory. None if the directory did not exist.
    copy: Optional[str]
    # Top-level entries that the reset leaves alone.
    skip: FrozenSet[str]


class TrialSnapshot:
    """Puts the sandbox home and the work dirs back to their first-trial state.

    All trials of a job share one sandbox home, and all trials of a scenario
    share its work_dir. `save` copies these directories before the first
    trial. `restore` puts the copies back before each later trial, so a trial
    does not see the files of an earlier one. `remove` deletes the copies.

    The copies can hold credentials. They are in a directory next to the
    sandbox home that only the current user can open.
    """

    def __init__(self, fake_home: Optional[str], work_dirs: Iterable[str]):
        self._fake_home = os.path.abspath(fake_home) if fake_home else None
        self._work_dirs = sorted(set(work_dirs))
        self._root: Optional[str] = None  # The directory of the copies.
        self._saved: List[_SavedDir] = []

    def save(self) -> None:
        """Copies the sandbox home and the work dirs."""
        targets = []
        prefix, parent = "evalbench_trial_base_", None
        if self._fake_home:
            targets.append((self._fake_home, HOME_SKIP))
            # The name starts with the name of the home, so a copy that a
            # killed job left behind is easy to find next to it.
            prefix = os.path.basename(self._fake_home) + "_trial_base_"
            parent = os.path.dirname(self._fake_home)
        targets += [(work_dir, frozenset()) for work_dir in self._work_dirs]
        if not targets:
            return

        # mkdtemp makes a directory that only the current user can open.
        self._root = tempfile.mkdtemp(prefix=prefix, dir=parent)
        try:
            for index, (path, skip) in enumerate(targets):
                copy = None
                if os.path.isdir(path):
                    copy = os.path.join(self._root, str(index))
                    _copy_tree(path, copy, skip)
                self._saved.append(_SavedDir(path, copy, skip))
        except BaseException:
            # Do not leave a partial copy of the credentials behind.
            self.remove()
            raise
        logging.info(
            "Saved %d directories for the reset between trials in %s.",
            len(self._saved), self._root)

    def restore(self) -> None:
        """Puts every directory back to its state at `save`."""
        for saved in self._saved:
            if saved.copy is None or not os.path.isdir(saved.path):
                # The directory did not exist at `save`, or a trial put
                # something else in its place.
                if os.path.lexists(saved.path):
                    _remove(saved.path)
            else:
                for entry in os.scandir(saved.path):
                    if entry.name not in saved.skip:
                        _remove(entry.path)
            if saved.copy is not None:
                _copy_tree(saved.copy, saved.path)
        logging.info("Reset %d directories before the trial.", len(self._saved))

    def remove(self) -> None:
        """Deletes the copies."""
        if self._root is None:
            return
        try:
            _remove(self._root)
        except OSError as e:
            logging.warning(
                "Could not delete the trial snapshot at %s: %s", self._root, e)
        self._root = None
        self._saved = []


def check_resettable(work_dir: str, protected: Iterable[str]) -> None:
    """Raises ValueError if a reset of work_dir would delete a protected directory.

    A reset deletes everything in work_dir, so work_dir must not be a
    protected directory or contain one.
    """
    real_work_dir = os.path.realpath(work_dir)
    for directory in protected:
        real_directory = os.path.realpath(directory)
        if os.path.commonpath([real_work_dir, real_directory]) == real_work_dir:
            raise ValueError(
                f"work_dir {work_dir} cannot be reset between trials, because "
                f"it is or contains {directory}. Use a sub-directory as the "
                f"work_dir, or set num_trials: 1 on the scenario.")


def _copy_tree(
    source: str, target: str, skip: FrozenSet[str] = frozenset()
) -> None:
    """Copies a directory tree. Leaves out the top-level entries in `skip`.

    Unlike shutil.copytree, this keeps the owner of each entry and leaves out
    sockets, pipes, and devices. Links stay links.
    """
    os.makedirs(target, exist_ok=True)
    for entry in os.scandir(source):
        if entry.name in skip:
            continue
        copy = os.path.join(target, entry.name)
        if entry.is_symlink():
            os.symlink(os.readlink(entry.path), copy)
            _copy_owner(entry.path, copy)
        elif entry.is_dir():
            _copy_tree(entry.path, copy)
        elif entry.is_file():
            shutil.copy2(entry.path, copy)
            _copy_owner(entry.path, copy)
    # Last, because a read-only mode would block the copy of the entries.
    shutil.copystat(source, target)
    _copy_owner(source, target)


def _copy_owner(source: str, target: str) -> None:
    """Gives target the owner of source. Does not follow links.

    Only root can change an owner. As root, some harnesses run the agent as
    another user, who must still own the restored files.
    """
    if os.geteuid() == 0:
        info = os.lstat(source)
        os.lchown(target, info.st_uid, info.st_gid)


def _remove(path: str) -> None:
    """Deletes a file, a link, or a directory tree. Does not follow links.

    Unlike shutil.rmtree, this also deletes read-only directories.
    """
    if os.path.islink(path) or not os.path.isdir(path):
        os.unlink(path)
        return
    # A read-only directory blocks the delete of its entries.
    os.chmod(path, stat.S_IRWXU)
    for entry in os.scandir(path):
        _remove(entry.path)
    os.rmdir(path)
