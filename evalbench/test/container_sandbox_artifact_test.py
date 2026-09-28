"""Tests for shipping a containerized case's sandbox to GCS.

Under containerization the case pod -- and its `fake_home` -- is gone by the
time the eval server's `GcsReporter` runs, so the case runner uploads the
sandbox itself. These cover that upload, the row rewriting that keeps the
server-side reporter from chasing a container-local path, and the
failure-path guarantees (an upload problem never fails a case; a crashed case
still ships its sandbox).
"""

import os
import shutil
import tempfile
import unittest
import zipfile
from unittest import mock

import pandas as pd

from container import case_runner
from container.backend import CaseResult
from container.spec import EvalCaseSpec
from reporting.gcs_artifact import GcsReporter, zip_and_upload_dir
from reporting.report import STORETYPE


class _FakeBlob:

    def __init__(self, bucket, name):
        self._bucket = bucket
        self.name = name

    def upload_from_filename(self, path):
        if self._bucket.fail:
            raise RuntimeError("gcs down")
        with open(path, "rb") as f:
            self._bucket.uploads[self.name] = f.read()


class _FakeBucket:

    def __init__(self, name="bkt", fail=False):
        self.name = name
        self.fail = fail
        self.uploads = {}

    def blob(self, name):
        return _FakeBlob(self, name)


def _spec(case_id="cuj-01", job_id="job-1"):
    return EvalCaseSpec(
        case_id=case_id,
        job_id=job_id,
        run_time_iso="2026-09-10T12:00:00",
        scenario={"id": case_id, "starting_prompt": "hi"},
        config={},
    )


class _SandboxTestBase(unittest.TestCase):

    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.tmp, ignore_errors=True)
        self.fake_home = os.path.join(self.tmp, "fake_home")
        os.makedirs(os.path.join(self.fake_home, "work"))
        with open(os.path.join(self.fake_home, "work", "out.sql"), "w") as f:
            f.write("select 1;")
        self.bucket = _FakeBucket()

    def _publish(self, config, rows, bucket=None):
        bucket = bucket or self.bucket
        return case_runner._publish_sandbox(
            config, _spec(), self.fake_home, rows,
            bucket_factory=lambda name: bucket)

    @staticmethod
    def _config(**gcs):
        return {"reporting": {"gcs_artifacts": {"bucket": "bkt", **gcs}}}


class TestPublishSandbox(_SandboxTestBase):

    def test_uploads_under_the_gcs_reporter_layout(self):
        rows = [{"eval_id": "cuj-01", "fake_home": self.fake_home}]
        uri = self._publish(self._config(), rows)

        self.assertEqual(uri, "gs://bkt/results/job-1/cuj-01.zip")
        self.assertIn("results/job-1/cuj-01.zip", self.bucket.uploads)
        self.assertEqual(rows[0]["artifact_uri"], uri)

    def test_honours_a_custom_path_prefix(self):
        uri = self._publish(self._config(path_prefix="hillclimb"), [])
        self.assertEqual(uri, "gs://bkt/hillclimb/job-1/cuj-01.zip")

    def test_clears_the_container_local_fake_home(self):
        # Left set, the eval server's GcsReporter would try to zip a path that
        # only ever existed inside the (now deleted) case pod.
        rows = [{"eval_id": "cuj-01", "fake_home": self.fake_home}]
        self._publish(self._config(), rows)
        self.assertIsNone(rows[0]["fake_home"])

    def test_clears_fake_home_even_without_gcs_configured(self):
        rows = [{"eval_id": "cuj-01", "fake_home": self.fake_home}]
        uri = self._publish({}, rows)
        self.assertIsNone(uri)
        self.assertIsNone(rows[0]["fake_home"])
        self.assertNotIn("artifact_uri", rows[0])
        self.assertEqual(self.bucket.uploads, {})

    def test_a_delegated_block_is_not_uploaded(self):
        uri = self._publish(self._config(delegated=True), [])
        self.assertIsNone(uri)
        self.assertEqual(self.bucket.uploads, {})

    def test_a_block_without_a_bucket_is_not_uploaded(self):
        uri = self._publish({"reporting": {"gcs_artifacts": {}}}, [])
        self.assertIsNone(uri)

    def test_a_failed_upload_never_raises(self):
        rows = [{"eval_id": "cuj-01", "fake_home": self.fake_home}]
        uri = self._publish(self._config(), rows, bucket=_FakeBucket(fail=True))
        self.assertIsNone(uri)
        self.assertIsNone(rows[0]["fake_home"])
        self.assertNotIn("artifact_uri", rows[0])

    def test_a_bucket_factory_that_raises_never_raises(self):
        def boom(name):
            raise RuntimeError("no credentials")

        uri = case_runner._publish_sandbox(
            self._config(), _spec(), self.fake_home, [], bucket_factory=boom)
        self.assertIsNone(uri)

    def test_a_missing_sandbox_is_skipped(self):
        shutil.rmtree(self.fake_home)
        self.assertIsNone(self._publish(self._config(), []))
        self.assertEqual(self.bucket.uploads, {})


class TestZipContents(_SandboxTestBase):

    def test_zip_skips_hidden_and_dependency_dirs(self):
        os.makedirs(os.path.join(self.fake_home, "node_modules", "x"))
        with open(os.path.join(self.fake_home, "node_modules", "x", "i.js"), "w") as f:
            f.write("x")
        with open(os.path.join(self.fake_home, ".secret"), "w") as f:
            f.write("x")

        zip_and_upload_dir(self.fake_home, self.bucket, "a.zip")

        zpath = os.path.join(self.tmp, "a.zip")
        with open(zpath, "wb") as f:
            f.write(self.bucket.uploads["a.zip"])
        with zipfile.ZipFile(zpath) as z:
            names = z.namelist()
        self.assertEqual(names, [os.path.join("work", "out.sql")])


class TestGcsReporterIgnoresContainerizedRows(unittest.TestCase):

    @mock.patch("reporting.gcs_artifact.storage.Client")
    def test_rows_with_null_fake_home_are_not_rezipped(self, client):
        reporter = GcsReporter({"bucket": "bkt"}, "job-1", None)
        results = pd.DataFrame.from_dict(
            [
                {"eval_id": "a", "fake_home": None,
                 "artifact_uri": "gs://bkt/results/job-1/a.zip"},
                {"eval_id": "b", "fake_home": None},
            ],
            dtype="string",
        )
        with mock.patch("reporting.gcs_artifact.zip_and_upload_dir") as upload:
            reporter.store(results, STORETYPE.EVALS)
        upload.assert_not_called()


class TestRunCaseReporting(_SandboxTestBase):
    """`run_case` with the real spec plumbing and a stand-in evaluator."""

    def _run_with(self, evaluate):
        fake_home = self.fake_home

        class FakeEvaluator:

            def __init__(self, config):
                self.generator = mock.Mock(fake_home=fake_home)

            def evaluate(self, items, job_id, run_time):
                return evaluate()

        spec = EvalCaseSpec.build(
            scenario={"id": "cuj-01", "starting_prompt": "hi"},
            config=self._config(),
            job_id="job-1",
            run_time_iso="2026-09-10T12:00:00",
        )
        case_dir = spec.write_to_dir(os.path.join(self.tmp, "case"))
        with mock.patch(
            "evaluator.agentevaluator.AgentEvaluator", FakeEvaluator
        ), mock.patch.object(
            case_runner, "_publish_sandbox", wraps=case_runner._publish_sandbox
        ) as publish, mock.patch(
            "google.cloud.storage.Client"
        ) as client:
            client.return_value.bucket.return_value = self.bucket
            payload = case_runner.run_case(case_dir)
        return payload, publish

    def test_a_scored_case_carries_its_artifact_uri(self):
        payload, _ = self._run_with(
            lambda: ([{"eval_id": "cuj-01", "fake_home": self.fake_home}], [{}]))
        self.assertIsNone(payload["error"])
        self.assertEqual(
            payload["artifact_uri"], "gs://bkt/results/job-1/cuj-01.zip")
        self.assertEqual(
            payload["agent_results"][0]["artifact_uri"], payload["artifact_uri"])

    def test_a_case_with_no_rows_is_an_error_not_a_silent_drop(self):
        payload, _ = self._run_with(lambda: ([], []))
        self.assertIn("produced no result rows", payload["error"])

    def test_a_crashed_case_still_ships_its_sandbox(self):
        def explode():
            raise RuntimeError("agent blew up")

        payload, publish = self._run_with(explode)
        self.assertIn("agent blew up", payload["error"])
        self.assertEqual(
            payload["artifact_uri"], "gs://bkt/results/job-1/cuj-01.zip")
        publish.assert_called_once()


class TestCaseResultCarriesArtifact(unittest.TestCase):

    def test_from_payload_reads_artifact_uri(self):
        result = CaseResult.from_payload(
            "c", {"error": "boom", "artifact_uri": "gs://b/x.zip"})
        self.assertEqual(result.artifact_uri, "gs://b/x.zip")
        self.assertFalse(result.ok)


if __name__ == "__main__":
    unittest.main()
