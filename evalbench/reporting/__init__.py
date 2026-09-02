from typing import Any

from .bqstore import BigQueryReporter
from .csv import CsvReporter
from .gcs_artifact import GcsReporter
from .remote_reporter import RemoteReporter
from .report import Reporter


DEFAULT_REPORTERS: dict[str, type[Reporter]] = {
    "bigquery": BigQueryReporter,
    "csv": CsvReporter,
    "gcs": GcsReporter,
    "gcs_artifacts": GcsReporter,
    "artifacts": GcsReporter,
}


def get_reporters(
    reporting_config: dict[str, Any] | None,
    job_id: str,
    run_time: Any,
) -> list[Reporter]:
    """Resolves and instantiates reporter instances for a given run config."""
    reporters: list[Reporter] = []
    if not reporting_config or not isinstance(reporting_config, dict):
        return reporters

    for key, cfg in reporting_config.items():
        if isinstance(cfg, dict) and cfg.get("delegated", False):
            reporters.append(RemoteReporter(key, cfg, job_id, run_time))
        elif key in DEFAULT_REPORTERS:
            reporters.append(DEFAULT_REPORTERS[key](cfg, job_id, run_time))

    return reporters
