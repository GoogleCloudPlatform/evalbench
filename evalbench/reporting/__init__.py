from .csv import CsvReporter
from .bqstore import BigQueryReporter
from .report import Reporter
from .gcs_artifact import GcsReporter
from .remote_artifact_reporter import RemoteArtifactReporter


def get_reporters(reporting_config, job_id, run_time) -> list[Reporter]:
    reporters: list[Reporter] = []
    if not reporting_config:
        return reporters
    if "bigquery" in reporting_config:
        reporters.append(
            BigQueryReporter(reporting_config["bigquery"], job_id, run_time)
        )
    if "csv" in reporting_config:
        reporters.append(CsvReporter(
            reporting_config["csv"], job_id, run_time))
    if "remote_artifacts" in reporting_config:
        reporters.append(RemoteArtifactReporter(
            reporting_config["remote_artifacts"], job_id, run_time))
    else:
        gcs_cfg = reporting_config.get("gcs") or reporting_config.get("gcs_artifacts") or reporting_config.get("artifacts")
        if gcs_cfg is not None:
            if isinstance(gcs_cfg, dict) and gcs_cfg.get("remote", False):
                reporters.append(RemoteArtifactReporter(gcs_cfg, job_id, run_time))
            else:
                reporters.append(GcsReporter(gcs_cfg, job_id, run_time))
    return reporters
