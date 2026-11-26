"""Cached listing of run directories under the results mount.

The mount is GCS FUSE holding tens of thousands of run directories, so
os.listdir + os.path.isdir costs a stat round trip per entry. os.scandir
carries the entry type through from the listing and avoids them; the TTL
collapses the repeated walks the render path makes.
"""

import logging
import os
import threading
import time

CACHE_TTL_SECONDS = float(os.environ.get("RUN_DIRS_CACHE_TTL", "60"))

_lock = threading.Lock()
_cache = {"path": None, "dirs": [], "at": 0.0}


def list_run_directories(results_dir, force=False):
    """Return the names of run directories directly under results_dir.

    Cached for CACHE_TTL_SECONDS. On a failed refresh a previous listing for the
    same path is returned rather than an empty one, so a transient FUSE error
    does not blank the dashboard.
    """
    now = time.monotonic()

    with _lock:
        if (
            not force
            and _cache["path"] == results_dir
            and now - _cache["at"] < CACHE_TTL_SECONDS
        ):
            return list(_cache["dirs"])

    try:
        dirs = []
        with os.scandir(results_dir) as entries:
            for entry in entries:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        dirs.append(entry.name)
                except OSError:
                    # Skipping one bad entry beats failing the whole listing.
                    continue
    except FileNotFoundError:
        dirs = []
    except OSError:
        logging.exception("Failed to list run directories in %s", results_dir)
        with _lock:
            if _cache["path"] == results_dir:
                return list(_cache["dirs"])
        return []

    with _lock:
        _cache.update({"path": results_dir, "dirs": dirs, "at": now})

    return list(dirs)


def resolve_run_dir(results_dir, name):
    """Return the path of run directory `name` in results_dir, or None.

    `name` comes from the browser (a query parameter or the Mesop state), so
    it is untrusted. It must be one path component, and the directory must be
    inside results_dir after symlinks are resolved.
    """
    if not isinstance(name, str) or name in ("", ".", ".."):
        return None
    if "\x00" in name or os.path.basename(name) != name:
        return None
    root = os.path.realpath(results_dir)
    real_path = os.path.realpath(os.path.join(root, name))
    try:
        if real_path == root or os.path.commonpath([root, real_path]) != root:
            return None
    except ValueError:
        return None
    return os.path.join(results_dir, name)


def invalidate():
    """Drop the cached listing so the next call re-walks the mount."""
    with _lock:
        _cache.update({"path": None, "dirs": [], "at": 0.0})
