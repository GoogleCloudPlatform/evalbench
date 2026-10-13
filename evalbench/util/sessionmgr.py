import os
import re
from threading import Thread, Lock
import logging
import time
from absl import app
import uuid

SESSION_RESOURCES_PATH = "/tmp_sessions/"

# Session IDs come from client metadata and are used as directory names.
_SESSION_ID_RE = re.compile(r"[A-Za-z0-9_.-]{1,128}")


def validate_session_id(session_id) -> str:
    """Returns session_id. Raises ValueError if it is unsafe as a dir name."""
    if (
        not isinstance(session_id, str)
        or session_id in (".", "..")
        or not _SESSION_ID_RE.fullmatch(session_id)
    ):
        raise ValueError(f"Invalid session_id: {session_id!r}")
    return session_id


def safe_resource_path(session_id, address) -> str:
    """Returns the resource path. Raises ValueError if it is outside the session dir."""
    base = os.path.realpath(
        os.path.join(SESSION_RESOURCES_PATH, validate_session_id(session_id))
    )
    if not address or "\x00" in address or os.path.isabs(address):
        raise ValueError(f"Invalid resource address: {address!r}")
    # realpath resolves "../" and symlinks before the containment check.
    full_path = os.path.realpath(os.path.join(base, address))
    if full_path == base or os.path.commonpath([base, full_path]) != base:
        raise ValueError(
            f"Resource address escapes session directory: {address!r}")
    return full_path


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
        try:
            if not os.path.exists(SESSION_RESOURCES_PATH):
                return
            for sid in os.listdir(SESSION_RESOURCES_PATH):
                try:
                    validate_session_id(sid)
                except ValueError:
                    logging.warning(
                        f"Skipping invalid session directory {sid!r} on disk.")
                    continue
                dir_path = os.path.join(SESSION_RESOURCES_PATH, sid)
                if os.path.isdir(dir_path):
                    mtime = os.path.getmtime(dir_path)
                    logging.info(f"Loading session {sid} from disk with mtime {mtime}.")
                    self.sessions[sid] = {
                        "create_ts": mtime,
                        "session_id": sid,
                    }
        except Exception as e:
            logging.error(f"Error loading sessions from disk: {e}")

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
        # Validate all paths first so a bad resource causes no partial write.
        paths = [
            (safe_resource_path(session_id, resource.address), resource)
            for resource in resources
        ]
        for full_path, resource in paths:
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(resource.content)

    def prune_resource_files(self, session_id):
        path = os.path.join(
            SESSION_RESOURCES_PATH, validate_session_id(session_id))
        if not os.path.exists(path):
            return
        for root, dirs, files in os.walk(path, topdown=False):
            for file in files:
                file_path = os.path.join(root, file)
                os.remove(file_path)
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                if os.path.islink(dir_path):
                    os.unlink(dir_path)
                else:
                    os.rmdir(dir_path)
        os.rmdir(path)

    def create_session(self, session_id):
        validate_session_id(session_id)
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
