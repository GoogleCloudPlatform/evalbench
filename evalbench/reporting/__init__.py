from .csv import CsvReporter
from .bqstore import BigQueryReporter
from .report import Reporter


def get_reporters(reporting_config, job_id, run_time) -> list[Reporter]:
    reporters: list[Reporter] = []
    if "bigquery" in reporting_config:
        reporters.append(
            BigQueryReporter(reporting_config["bigquery"], job_id, run_time)
        )
    if "csv" in reporting_config:
        reporters.append(CsvReporter(reporting_config["csv"], job_id, run_time))
    return reporters
