import os
import shutil
from threading import Thread, Lock
import logging
import time
from absl import app
import uuid

# Where client-uploaded resources (run/model configs, scenario JSON, rubric
# YAMLs, validator scripts, env_files) are staged per session. These are
# evaluator-only: the agent must never be able to reach them. Keep this root
# out of /tmp* and, above all, out of SESSION_SANDBOX_PATH -- the two trees
# used to share `/tmp_sessions/<sid>/`, which put the rubrics and scenario
# files one `ls ..` away from the agent's cwd.
SESSION_RESOURCES_PATH = "/var/lib/evalbench/sessions/"

# Where the agent CLIs get their per-session `fake_home` (HOME and default
# cwd). Mounted on the session PVC in GKE. Nothing under this root is
# secret; everything the agent needs is copied in by the generators.
SESSION_SANDBOX_PATH = "/tmp_sessions/"


class RWLock:
    def __init__(self):
        self.lock = Lock()
        self.write_lock = Lock()
        self.readers = 0

    def acquire_read(self):
        with self.lock:
            self.readers += 1
            if self.readers == 1:
                self.write_lock.acquire()

    def release_read(self):
        with self.lock:
            self.readers -= 1
            if self.readers == 0:
                self.write_lock.release()

    def acquire_write(self):
        self.write_lock.acquire()

    def release_write(self):
        self.write_lock.release()


class SessionManager:
    def __init__(
        self,
    ):
        self.running = True
        self.sessions = {}
        self.ttl = 10800
        self.lock = RWLock()
        self.load_sessions_from_disk()
        logging.debug("Starting reaper...")
        reaper = Thread(target=self.reaper, args=[])
        reaper.daemon = True
        reaper.start()

    def load_sessions_from_disk(self):
        """Re-registers sessions left on disk so the reaper can expire them.

        Both roots are scanned: the resources root lives on the container
        filesystem, while the sandbox root is on the session PVC and is what
        actually survives a pod restart (and holds the bulk of the disk).
        """
        for base in (SESSION_RESOURCES_PATH, SESSION_SANDBOX_PATH):
            try:
                if not os.path.exists(base):
                    continue
                for sid in os.listdir(base):
                    dir_path = os.path.join(base, sid)
                    if not os.path.isdir(dir_path) or sid in self.sessions:
                        continue
                    mtime = os.path.getmtime(dir_path)
                    logging.info(
                        f"Loading session {sid} from disk with mtime {mtime}.")
                    self.sessions[sid] = {
                        "create_ts": mtime,
                        "session_id": sid,
                    }
            except Exception as e:
                logging.error(
                    f"Error loading sessions from disk under {base}: {e}")

    def set_ttl(self, ttl):
        self.ttl = ttl

    def get_ttl(self):
        return self.ttl

    def get_session(self, session_id):
        self.lock.acquire_read()
        try:
            return self.sessions.get(session_id)
        finally:
            self.lock.release_read()

    def write_resource_files(self, session_id, resources):
        session_root = os.path.join(SESSION_RESOURCES_PATH, session_id)
        # 0700 so that, once agent CLIs run as a non-root user, the tree is
        # not even listable from the sandbox. Harmless until then.
        os.makedirs(session_root, mode=0o700, exist_ok=True)
        os.chmod(session_root, 0o700)
        for resource in resources:
            full_path = os.path.join(session_root, resource.address)
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(resource.content)

    def prune_resource_files(self, session_id):
        """Removes everything a session left on disk, in both roots."""
        for base in (SESSION_RESOURCES_PATH, SESSION_SANDBOX_PATH):
            path = os.path.join(base, session_id)
            if not os.path.lexists(path):
                continue
            if os.path.islink(path):
                os.unlink(path)
                continue
            # rmtree does not follow symlinks, so a skill linked from the
            # sandbox into the resources tree is unlinked, not traversed.
            shutil.rmtree(path, ignore_errors=True)

    def create_session(self, session_id):
        self.lock.acquire_write()
        try:
            if session_id in self.sessions:
                logging.info(f"Session {session_id} already exists.")
                return self.sessions[session_id]
            logging.info(f"Create session {session_id}.")
            self.sessions[session_id] = {
                "create_ts": time.time(), "session_id": session_id}
            return self.sessions[session_id]
        finally:
            self.lock.release_write()

    def get_sessions(self):
        self.lock.acquire_read()
        try:
            return dict(self.sessions)
        finally:
            self.lock.release_read()

    def delete_session(self, session_id):
        self.lock.acquire_write()
        try:
            if session_id in self.sessions:
                del self.sessions[session_id]
        finally:
            self.lock.release_write()

    def shutdown(self):
        self.running = False

    def reaper(self):
        while self.running:
            now = time.time()
            self.lock.acquire_read()
            try:
                to_delete = [sid for sid, s in self.sessions.items() if now - s["create_ts"] > self.ttl]
            finally:
                self.lock.release_read()

            for sid in to_delete:
                logging.info(f"Delete session {sid}.")
                self.delete_session(sid)
                self.prune_resource_files(sid)
            time.sleep(10)
