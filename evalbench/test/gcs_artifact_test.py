import unittest
from unittest.mock import patch, MagicMock
import pandas as pd
import datetime
from reporting.report import STORETYPE
from reporting.gcs_artifact import GcsReporter


class TestGcsReporter(unittest.TestCase):
    def setUp(self):
        self.reporting_config = {"bucket": "test-bucket"}
        self.job_id = "test-job-id"
        self.run_time = datetime.datetime.now()

    @patch('reporting.gcs_artifact.storage.Client')
    def test_init(self, mock_storage_client):
        reporter = GcsReporter(self.reporting_config, self.job_id, self.run_time)
        self.assertEqual(reporter.bucket_name, "test-bucket")
        self.assertTrue(mock_storage_client.called)

    @patch('reporting.gcs_artifact.storage.Client')
    def test_store_not_evals(self, mock_storage_client):
        reporter = GcsReporter(self.reporting_config, self.job_id, self.run_time)
        results = pd.DataFrame({"fake_home": ["/tmp/dir1"], "eval_id": ["1"]})
        reporter.store(results, STORETYPE.CONFIGS)
        # Should return early and not call bucket
        mock_storage_client.return_value.bucket.assert_not_called()

    @patch('reporting.gcs_artifact.storage.Client')
    def test_store_missing_bucket(self, mock_storage_client):
        reporter = GcsReporter({}, self.job_id, self.run_time)
        results = pd.DataFrame({"fake_home": ["/tmp/dir1"], "eval_id": ["1"]})
        reporter.store(results, STORETYPE.EVALS)
        mock_storage_client.return_value.bucket.assert_not_called()

    @patch('reporting.gcs_artifact.storage.Client')
    def test_store_missing_eval_id(self, mock_storage_client):
        reporter = GcsReporter(self.reporting_config, self.job_id, self.run_time)
        results = pd.DataFrame({"fake_home": ["/tmp/dir1"]})
        reporter.store(results, STORETYPE.EVALS)
        mock_storage_client.return_value.bucket.assert_not_called()

    @patch('reporting.gcs_artifact.os.path.getsize')
    @patch('reporting.gcs_artifact.storage.Client')
    @patch('reporting.gcs_artifact.os.path.exists')
    @patch('reporting.gcs_artifact.zipfile.ZipFile')
    @patch('reporting.gcs_artifact.tempfile.NamedTemporaryFile')
    @patch('reporting.gcs_artifact.os.remove')
    def test_store_isolated(self, mock_remove, mock_tempfile, mock_zipfile, mock_exists, mock_storage_client, mock_getsize):
        mock_exists.return_value = True
        mock_getsize.return_value = 1024

        # Mock temp file
        mock_temp = MagicMock()
        mock_temp.name = "/tmp/temp.zip"
        mock_tempfile.return_value.__enter__.return_value = mock_temp

        # Mock storage
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_storage_client.return_value.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        reporter = GcsReporter(self.reporting_config, self.job_id, self.run_time)

        results = pd.DataFrame({
            "fake_home": ["/tmp/dir1", "/tmp/dir2"],
            "eval_id": ["eval1", "eval2"]
        })

        reporter.store(results, STORETYPE.EVALS)

        # Verify uploads
        self.assertEqual(mock_bucket.blob.call_count, 2)
        mock_bucket.blob.assert_any_call("results/test-job-id/eval1.zip")
        mock_bucket.blob.assert_any_call("results/test-job-id/eval2.zip")
        self.assertEqual(mock_blob.upload_from_filename.call_count, 2)

    @patch('reporting.gcs_artifact.os.path.getsize')
    @patch('reporting.gcs_artifact.storage.Client')
    @patch('reporting.gcs_artifact.os.path.exists')
    @patch('reporting.gcs_artifact.zipfile.ZipFile')
    @patch('reporting.gcs_artifact.tempfile.NamedTemporaryFile')
    @patch('reporting.gcs_artifact.os.remove')
    def test_store_shared(self, mock_remove, mock_tempfile, mock_zipfile, mock_exists, mock_storage_client, mock_getsize):
        mock_exists.return_value = True
        mock_getsize.return_value = 1024

        # Mock temp file
        mock_temp = MagicMock()
        mock_temp.name = "/tmp/temp.zip"
        mock_tempfile.return_value.__enter__.return_value = mock_temp

        # Mock storage
        mock_bucket = MagicMock()
        mock_blob = MagicMock()
        mock_storage_client.return_value.bucket.return_value = mock_bucket
        mock_bucket.blob.return_value = mock_blob

        reporter = GcsReporter(self.reporting_config, self.job_id, self.run_time)

        results = pd.DataFrame({
            "fake_home": ["/tmp/dir1", "/tmp/dir1"],
            "eval_id": ["eval1", "eval2"]
        })

        reporter.store(results, STORETYPE.EVALS)

        # Verify upload (should be only 1 upload for shared dir)
        self.assertEqual(mock_bucket.blob.call_count, 1)
        mock_bucket.blob.assert_called_once_with("results/test-job-id/fake_home.zip")
        mock_blob.upload_from_filename.assert_called_once_with("/tmp/temp.zip")


if __name__ == '__main__':
    unittest.main()
