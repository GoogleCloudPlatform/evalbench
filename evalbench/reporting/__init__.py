from .csv import CsvReporter
from .bqstore import BigQueryReporter
from .report import Reporter
from .gcs_artifact import GcsReporter


def get_reporters(reporting_config, job_id, run_time) -> list[Reporter]:
    reporters: list[Reporter] = []
    if not reporting_config:
        return reporters

    # Check for delegated reporters across the reverse stream
    for key, cfg in reporting_config.items():
        if isinstance(cfg, dict) and cfg.get("delegated", False):
            from .remote_reporter import RemoteReporter
            reporters.append(RemoteReporter(key, cfg, job_id, run_time))

    if "bigquery" in reporting_config and not reporting_config.get("bigquery", {}).get("delegated", False):
        reporters.append(
            BigQueryReporter(reporting_config["bigquery"], job_id, run_time)
        )
    if "csv" in reporting_config and not reporting_config.get("csv", {}).get("delegated", False):
        reporters.append(CsvReporter(
            reporting_config["csv"], job_id, run_time))
    if "gcs_artifacts" in reporting_config and not reporting_config.get("gcs_artifacts", {}).get("delegated", False):
        reporters.append(GcsReporter(
            reporting_config["gcs_artifacts"], job_id, run_time))
    return reporters
