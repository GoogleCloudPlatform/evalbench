import os
from threading import Thread
import logging
import time
from absl import app
import uuid

SESSION_RESOURCES_PATH = "/tmp_session_files/"


class SessionManager:
    def __init__(
        self,
    ):
        self.running = True
        self.sessions = {}
        self.ttl = 3600
        logging.debug("Starting reaper...")
        reaper = Thread(target=self.reaper, args=[])
        reaper.daemon = True
        reaper.start()

    def set_ttl(self, ttl):
        self.ttl = ttl

    def get_ttl(self):
        return self.ttl

    def get_session(self, session_id):
        return self.sessions[session_id]

    def write_resource_files(self, session_id, resources):
        for resource in resources:
            full_path = os.path.join(
                SESSION_RESOURCES_PATH, session_id, resource.address
            )
            os.makedirs(os.path.dirname(full_path), exist_ok=True)
            with open(full_path, "wb") as f:
                f.write(resource.content)

    def prune_resource_files(self, session_id):
        path = os.path.join(SESSION_RESOURCES_PATH, session_id)
        if not os.path.exists(path):
            return
        for root, dirs, files in os.walk(path, topdown=False):
            for file in files:
                file_path = os.path.join(root, file)
                os.remove(file_path)
            for dir in dirs:
                dir_path = os.path.join(root, dir)
                os.rmdir(dir_path)
        os.rmdir(path)

    def create_session(self, session_id):
        if session_id in self.sessions.keys():
            logging.info(f"Session {session_id} already exists.")
            return self.sessions[session_id]
        logging.info(f"Create session {session_id}.")
        self.sessions[session_id] = {"create_ts": time.time(), "session_id": session_id}
        return self.sessions[session_id]

    def get_sessions(self):
        return self.sessions

    def delete_session(self, session_id):
        del self.sessions[session_id]

    def shutdown(self):
        self.running = False

    def reaper(self):
        old_sessions = []
        while self.running:
            logging.debug(f"Reaper cycle: {len(self.sessions)}")
            for session_id in self.sessions.keys():
                if time.time() - self.sessions[session_id]["create_ts"] > self.ttl:
                    old_sessions.append(session_id)
            for session_id in old_sessions:
                logging.info(f"Delete session {session_id}.")
                self.delete_session(session_id)
                self.prune_resource_files(session_id)
                old_sessions.remove(session_id)
            time.sleep(1)
