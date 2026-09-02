from .csv import CsvReporter
from .bqstore import BigQueryReporter
from .report import Reporter
from .gcs_artifact import GcsReporter
from .remote_reporter import RemoteReporter


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

    # Check for any delegated reporters
    for key, cfg in reporting_config.items():
        if key in ("bigquery", "csv"):
            continue
        if isinstance(cfg, dict) and cfg.get("delegated", False):
            reporters.append(RemoteReporter(key, cfg, job_id, run_time))
        elif key in ("gcs", "gcs_artifacts", "artifacts"):
            reporters.append(GcsReporter(cfg, job_id, run_time))

    return reporters
