"""Single-writer lease so only one instance precomputes at a time.

Supervisord starts a precompute loop in every Cloud Run instance, but they share
one results directory and the caches are rewritten whole, so a later write
silently discards an earlier one's rows while processed_dirs.json still records
those runs as done -- they are then never reconsidered.

Acquiring writes a holder id and timestamp, waits out the window in which a
competing write could still land, then reads back: a whole-file rewrite has one
winner and GCS reads are strongly consistent after it, so whoever reads its own
id back is the sole holder. Timestamps are wall-clock because they are compared
across hosts, where monotonic clocks are meaningless.
"""

import logging
import os
import socket
import threading
import time
import uuid

LEASE_FILENAME = "precompute.lease"

# Long enough to ride out a slow FUSE write, short enough that a killed holder
# does not stall precompute for long.
LEASE_TTL_SECONDS = float(os.environ.get("PRECOMPUTE_LEASE_TTL", "120"))
RENEW_INTERVAL_SECONDS = LEASE_TTL_SECONDS / 4

# Read-back is only meaningful once any competing write has had time to land.
GUARD_SECONDS = float(os.environ.get("PRECOMPUTE_LEASE_GUARD", "5"))

_HOLDER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def _lease_path(results_dir):
    return os.path.join(results_dir, LEASE_FILENAME)


def _read(results_dir):
    """Return (holder, timestamp), or (None, 0.0) if absent or unreadable."""
    try:
        with open(_lease_path(results_dir)) as f:
            holder, _, timestamp = f.read().partition("\n")
        return holder.strip(), float(timestamp.strip())
    except (OSError, ValueError):
        # A torn read lands here too; safe, because read-back must still pass.
        return None, 0.0


def _write(results_dir):
    with open(_lease_path(results_dir), "w") as f:
        f.write(f"{_HOLDER_ID}\n{time.time()}\n")


class PrecomputeLease:
    """Context manager guarding a precompute pass. Truthy only if held."""

    def __init__(self, results_dir):
        self.results_dir = results_dir
        self.acquired = False
        self._stop = threading.Event()
        self._renewer = None

    def acquire(self):
        holder, timestamp = _read(self.results_dir)
        age = time.time() - timestamp

        if holder and holder != _HOLDER_ID and age < LEASE_TTL_SECONDS:
            logging.info(
                "Precompute lease held by %s (renewed %.0fs ago); skipping pass",
                holder, age,
            )
            return False
        if holder and holder != _HOLDER_ID:
            logging.warning(
                "Taking over precompute lease from %s, stale for %.0fs", holder, age
            )

        try:
            _write(self.results_dir)
        except OSError:
            # Fail open: a dashboard that silently stops updating is worse than
            # the duplicated work this lease exists to prevent.
            logging.exception("Could not write precompute lease; running unguarded")
            self.acquired = True
            return True

        time.sleep(GUARD_SECONDS)

        winner, _ = _read(self.results_dir)
        if winner != _HOLDER_ID:
            logging.info("Lost precompute lease race to %s; skipping pass", winner)
            return False

        self.acquired = True
        self._renewer = threading.Thread(
            target=self._renew_loop, name="precompute-lease-renew", daemon=True
        )
        self._renewer.start()
        logging.info("Acquired precompute lease as %s", _HOLDER_ID)
        return True

    def _renew_loop(self):
        while not self._stop.wait(RENEW_INTERVAL_SECONDS):
            holder, _ = _read(self.results_dir)
            if holder and holder != _HOLDER_ID:
                # Stand down rather than fight the successor for the file.
                logging.warning("Precompute lease taken over by %s; stopping renewal", holder)
                return
            try:
                _write(self.results_dir)
            except OSError:
                logging.warning("Could not renew precompute lease")

    def release(self):
        self._stop.set()
        if self._renewer:
            self._renewer.join(timeout=RENEW_INTERVAL_SECONDS)
            self._renewer = None
        if not self.acquired:
            return
        self.acquired = False
        # Only clear the file if it is still ours, never a successor's.
        holder, _ = _read(self.results_dir)
        if holder == _HOLDER_ID:
            try:
                os.remove(_lease_path(self.results_dir))
            except OSError:
                logging.warning("Could not remove precompute lease file")

    def __enter__(self):
        self.acquire()
        return self

    def __exit__(self, *exc):
        self.release()
        return False

    def __bool__(self):
        return self.acquired
