import os
import tempfile
import zipfile
import logging
from typing import Any

from google.cloud import storage
import pandas as pd

from reporting.report import Reporter, STORETYPE


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

    _DEFAULT_PATH_PREFIX = "results"
    _EXCLUDED_DIRS = frozenset({".venv", "__pycache__", "node_modules", "venv"})

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
        logging.info(
            "GcsReporter._zip_and_upload: src_dir=%s, eval_id=%s",
            src_dir,
            eval_id,
        )
        if not os.path.exists(src_dir):
            logging.warning("Source directory %s does not exist.", src_dir)
            return

        zip_path: str | None = None
        try:
            with tempfile.NamedTemporaryFile(
                suffix=".zip", delete=False
            ) as tmp_file:
                zip_path = tmp_file.name

            with zipfile.ZipFile(zip_path, "w", zipfile.ZIP_DEFLATED) as zipf:
                for root, dirs, files in os.walk(src_dir):
                    # Exclude hidden directories and common heavy/cache directories
                    dirs[:] = [
                        d
                        for d in dirs
                        if not d.startswith(".")
                        and d not in self._EXCLUDED_DIRS
                    ]
                    for file in files:
                        # Exclude hidden files for privacy and size
                        if file.startswith("."):
                            continue
                        file_path = os.path.join(root, file)
                        arcname = os.path.relpath(file_path, src_dir)
                        zipf.write(file_path, arcname)

            blob_name = f"{self.path_prefix}/{self.job_id}/{eval_id}.zip"
            logging.info(
                "GcsReporter._zip_and_upload: Zip created. Size=%d bytes. Uploading to gs://%s/%s ...",
                os.path.getsize(zip_path),
                self.bucket_name,
                blob_name,
            )
            blob = bucket.blob(blob_name)
            blob.upload_from_filename(zip_path)
            logging.info(
                "Uploaded %s to gs://%s/%s",
                src_dir,
                self.bucket_name,
                blob_name,
            )

        except Exception:
            logging.exception("Failed to upload %s to GCS", src_dir)
        finally:
            if zip_path and os.path.exists(zip_path):
                os.remove(zip_path)
