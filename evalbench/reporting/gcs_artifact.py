import os
import tempfile
import zipfile
import logging
from typing import Any, Optional

from google.cloud import storage
import pandas as pd

from reporting.report import Reporter, STORETYPE

DEFAULT_PATH_PREFIX = "results"
# Directories never worth shipping: dependency trees and caches that dwarf the
# agent's actual work product.
EXCLUDED_DIRS = frozenset({".venv", "__pycache__", "node_modules", "venv"})


def artifact_blob_name(path_prefix: str, job_id: str, name: str) -> str:
    """The object name a run's sandbox zip is stored under."""
    return f"{path_prefix}/{job_id}/{name}.zip"


def zip_and_upload_dir(
    src_dir: str, bucket: storage.Bucket, blob_name: str
) -> Optional[str]:
    """Zips `src_dir` and uploads it to `bucket` as `blob_name`.

    Hidden files and directories and `EXCLUDED_DIRS` are skipped. Shared by
    `GcsReporter` (in-process runs, called from the eval server) and the
    containerized case runner (called from inside the case pod, whose sandbox
    is gone by the time the eval server reports), so both paths produce the
    same artifacts.

    Returns the `gs://` URI on success and None on any failure; an artifact
    upload must never fail the eval it documents.
    """
    logging.info(
        "zip_and_upload_dir: src_dir=%s, blob=%s", src_dir, blob_name)
    if not os.path.exists(src_dir):
        logging.warning("Source directory %s does not exist.", src_dir)
        return None

    zip_path: str | None = None
    try:
        with tempfile.NamedTemporaryFile(suffix=".zip", delete=False) as tmp_file:
            zip_path = tmp_file.name

        with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(src_dir):
                # Exclude hidden directories and common heavy/cache directories
                dirs[:] = [
                    d
                    for d in dirs
                    if not d.startswith(".") and d not in EXCLUDED_DIRS
                ]
                for file in files:
                    # Exclude hidden files for privacy and size
                    if file.startswith("."):
                        continue
                    file_path = os.path.join(root, file)
                    arcname = os.path.relpath(file_path, src_dir)
                    zipf.write(file_path, arcname)

        logging.info(
            "zip_and_upload_dir: Zip created. Size=%d bytes. Uploading to "
            "gs://%s/%s ...",
            os.path.getsize(zip_path),
            bucket.name,
            blob_name,
        )
        blob = bucket.blob(blob_name)
        blob.upload_from_filename(zip_path)
        uri = f"gs://{bucket.name}/{blob_name}"
        logging.info("Uploaded %s to %s", src_dir, uri)
        return uri

    except Exception:
        logging.exception("Failed to upload %s to GCS", src_dir)
        return None
    finally:
        if zip_path and os.path.exists(zip_path):
            os.remove(zip_path)


class GcsReporter(Reporter):
    """Reporter that zips and uploads scenario working directories to GCS.

    This reporter only processes `STORETYPE.EVALS` data. It captures the
    sandboxed workspace (`fake_home`) of an agent evaluation and uploads it
    as a zip file.

    Example `run_config.yaml` usage:
    ```yaml
    reporting:
      gcs_artifacts:
        bucket: 'my-evaluation-artifacts-bucket'
        path_prefix: 'optional_prefix'  # Defaults to 'results'
    ```
    """

    _DEFAULT_PATH_PREFIX = DEFAULT_PATH_PREFIX
    _EXCLUDED_DIRS = EXCLUDED_DIRS

    def __init__(
        self,
        reporting_config: dict[str, Any] | None,
        job_id: str,
        run_time: Any,
    ):
        """Initializes the GcsReporter.

        Args:
            reporting_config: Configuration dictionary for reporting.
            job_id: Unique identifier for the current evaluation job.
            run_time: Timestamp of the run.
        """
        super().__init__(reporting_config, job_id, run_time)
        self.bucket_name: str | None = (
            reporting_config.get("bucket") if reporting_config else None
        )
        logging.info(
            "GcsReporter: Initializing with bucket=%s", self.bucket_name
        )
        self.client = storage.Client()
        self.path_prefix: str = self.config.get(
            "path_prefix", self._DEFAULT_PATH_PREFIX
        )

    def store(self, results: pd.DataFrame, type: STORETYPE) -> None:
        """Zips and uploads working directories for completed evaluations.

        Args:
            results: DataFrame containing evaluation results.
            type: The type of data being stored (only EVALS is processed).
        """
        if type != STORETYPE.EVALS:
            return

        logging.info(
            "GcsReporter.store: processing type=%s, results len=%d",
            type,
            len(results) if results is not None else 0,
        )

        if not self.bucket_name:
            logging.warning("GCS bucket name not provided in config.")
            return

        if not isinstance(results, pd.DataFrame):
            logging.warning("Results is not a DataFrame, skipping GCS upload.")
            return

        if "fake_home" not in results.columns:
            logging.warning("No fake_home in results dataframe.")
            return

        if "eval_id" not in results.columns:
            logging.warning("No eval_id in results dataframe.")
            return

        logging.info(
            "GcsReporter.store: results columns: %s", results.columns.tolist()
        )

        bucket = self.client.bucket(self.bucket_name)
        unique_dirs = results["fake_home"].dropna().unique()

        if len(unique_dirs) == 1:
            fake_home = unique_dirs[0]
            self._zip_and_upload(fake_home, "fake_home", bucket)
        else:
            for fake_home in unique_dirs:
                rows = results[results["fake_home"] == fake_home]
                eval_id = rows["eval_id"].iloc[0]
                self._zip_and_upload(fake_home, eval_id, bucket)

    def _zip_and_upload(
        self, src_dir: str, eval_id: str, bucket: storage.Bucket
    ) -> None:
        """Zips the contents of src_dir and uploads it to the GCS bucket.

        Args:
            src_dir: The local directory to zip.
            eval_id: The evaluation ID used for the GCS object name.
            bucket: The GCS bucket to upload to.
        """
        zip_and_upload_dir(
            src_dir,
            bucket,
            artifact_blob_name(self.path_prefix, self.job_id, eval_id),
        )
