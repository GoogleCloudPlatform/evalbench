"""Single-writer lease so only one instance precomputes at a time.

Cloud Run scales the serving container up to maxScale, and supervisord starts a
precompute loop inside *every* instance. Each one then walks the same GCS-backed
results directory and processes the same backlog: five instances were observed
starting identical passes over the same 22k directories.

That is worse than wasted work. Both caches are rewritten whole, and a GCS
object write replaces the object rather than merging, so when two instances
finish a batch the later write silently discards the earlier one's rows. The
runs stay recorded in processed_dirs.json either way, so anything lost that way
is never reconsidered and never appears in trends again.

The lease is a small file in the results directory holding a holder id and a
wall-clock timestamp. Acquiring writes it, waits out the window in which a
competing write could still land, then reads it back: because a whole-file
rewrite has exactly one winner and GCS reads are strongly consistent after it,
whoever reads its own id back is the sole holder. The holder renews while it
works so a long pass keeps the lease, and the timestamp lets a survivor take
over if the holder dies -- which matters here, since a large backlog can get the
precompute process SIGKILLed mid-pass.

Wall-clock time is deliberate: monotonic clocks are not comparable across
instances, and the whole point is comparing timestamps written by other hosts.
"""

import logging
import os
import socket
import threading
import time
import uuid

LEASE_FILENAME = "precompute.lease"

# Long enough to ride out a slow FUSE write, short enough that a killed holder
# does not stall precompute for long. Renewal runs well inside it.
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
        # A torn read of a file being rewritten lands here too; treating it as
        # "no lease" is safe because the read-back check still has to pass.
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
            # Never let a lease problem stop precompute outright: a dashboard
            # that silently stops updating is a worse failure than duplicated
            # work, which is only what happened before this lease existed.
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
                # Someone judged us stale and took over. Two writers fighting
                # over the file is the exact thing this exists to prevent, so
                # stand down and re-acquire on the next pass.
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
        # Only clear the file if it is still ours; a successor's lease must not
        # be deleted by the instance it replaced.
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
