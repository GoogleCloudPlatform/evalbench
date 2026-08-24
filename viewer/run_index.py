"""Cached listing of run directories under the results mount.

The results directory is a GCS FUSE mount holding tens of thousands of run
directories. The obvious way to enumerate it --

    [d for d in os.listdir(p) if os.path.isdir(os.path.join(p, d))]

-- costs one FUSE stat round trip *per entry* on top of the listing itself,
because os.path.isdir cannot reuse anything os.listdir already learned. At
~25k entries that is ~25k round trips, and the Mesop render path ran that walk
three times per page, which pushed /__ui__ past the Cloud Run request timeout.

os.scandir carries the directory-entry type through from the listing, so
entry.is_dir() is answered without a second trip. The short TTL then collapses
the repeated walks within a single render (and across renders in a session)
down to one.
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

    Results are cached for CACHE_TTL_SECONDS. A missing results_dir yields an
    empty list. If a refresh fails but a previous listing for the same path is
    still held, the stale listing is returned rather than an empty one -- a
    transient FUSE error should not blank out the dashboard.
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
                    # Entry vanished mid-walk, or FUSE hiccuped on this one
                    # name. Skipping it beats failing the whole listing.
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


def invalidate():
    """Drop the cached listing so the next call re-walks the mount."""
    with _lock:
        _cache.update({"path": None, "dirs": [], "at": 0.0})
