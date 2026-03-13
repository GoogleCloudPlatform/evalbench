import socket
import subprocess
import time
import os
import logging
import signal
from google.cloud import spanner
from google.cloud.spanner_admin_database_v1.types import DatabaseDialect
from google.auth.credentials import AnonymousCredentials


class SpannerEmulatorManager:
    """Manages the lifecycle and provisioning of a local Cloud Spanner Emulator."""

    def __init__(self):
        self.process = None
        self.host_port = None
        self.grpc_port = None
        self.http_port = None

    def _get_free_port(self):
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.bind(('', 0))
            s.listen(1)
            port = s.getsockname()[1]
        return port

    def start(self):
        """Finds free ports and starts the gcloud spanner emulator."""
        try:
            subprocess.run(["gcloud", "--version"], stdout=subprocess.DEVNULL,
                           stderr=subprocess.DEVNULL, check=True)
        except (subprocess.CalledProcessError, FileNotFoundError):
            raise RuntimeError(
                "gcloud not found. Please install Google Cloud SDK to use managed Spanner Emulator.")

        self.grpc_port = self._get_free_port()
        self.http_port = self._get_free_port()
        self.host_port = f"localhost:{self.grpc_port}"

        logging.info(f"Starting Spanner Emulator on {self.host_port}...")

        # Start emulator
        # Note: The Emulator keeps all data in memory and does not free storage on DELETE/DROP.
        # To prevent OOM errors during extensive testing, we restart the
        # emulator for each database/dataset.
        cmd = [
            "gcloud", "emulators", "spanner", "start",
            f"--host-port={self.host_port}",
            f"--rest-port={self.http_port}"
        ]

        # Use start_new_session to ensure we can kill the process group
        self.process = subprocess.Popen(
            cmd,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            start_new_session=True)

        self._wait_for_ready()
        return self.host_port

    def _wait_for_ready(self, timeout=45):
        start = time.time()
        while time.time() - start < timeout:
            if self.process.poll() is not None:
                out, err = self.process.communicate()
                raise RuntimeError(f"Emulator failed to start: {err.decode()}")

            try:
                with socket.create_connection(("localhost", self.grpc_port), timeout=1):
                    time.sleep(2)  # Buffer for service initialization
                    return
            except (ConnectionRefusedError, socket.timeout):
                time.sleep(0.5)

        self.stop()
        raise RuntimeError("Timed out waiting for Spanner Emulator to start")

    def get_client_config(self, project_id):
        """Returns keyword arguments for spanner.Client to connect to this emulator."""
        if not self.host_port:
            raise RuntimeError("Emulator not started")
        return {
            "project": project_id,
            "credentials": AnonymousCredentials(),
            "client_options": {"api_endpoint": self.host_port}
        }

    def provision_database(
            self,
            project_id,
            instance_id,
            database_id,
            dialect="GOOGLE_STANDARD_SQL"):
        """Creates the instance and database in the running emulator."""
        if not self.host_port:
            raise RuntimeError("Emulator not started")

        logging.info(f"Provisioning {database_id} ({dialect}) in Emulator...")

        client = spanner.Client(**self.get_client_config(project_id))

        # Create Instance
        instance = client.instance(instance_id)
        if not instance.exists():
            config_name = f"{
                client.project_name}/instanceConfigs/emulator-config"
            instance = client.instance(
                instance_id, configuration_name=config_name)
            op = instance.create()
            op.result(timeout=120)

        # Create Database
        database = instance.database(database_id)
        if not database.exists():
            dialect_enum = DatabaseDialect.POSTGRESQL if dialect == "POSTGRESQL" else DatabaseDialect.GOOGLE_STANDARD_SQL
            op = instance.database(
                database_id, database_dialect=dialect_enum).create()
            op.result(timeout=120)

    def stop(self):
        """Stops the emulator process and its children."""
        if self.process:
            logging.info(f"Stopping Spanner Emulator on {self.host_port}...")
            try:
                os.killpg(os.getpgid(self.process.pid), signal.SIGTERM)
                self.process.wait(timeout=10)
            except (ProcessLookupError, subprocess.TimeoutExpired):
                try:
                    os.killpg(os.getpgid(self.process.pid), signal.SIGKILL)
                except BaseException:
                    pass
            self.process = None
