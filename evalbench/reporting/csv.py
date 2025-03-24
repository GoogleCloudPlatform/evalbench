import logging
from reporting.report import Reporter, STORETYPE
import os


class CsvReporter(Reporter):
    def __init__(self, reporting_config, job_id, run_time):
        super().__init__(reporting_config, job_id, run_time)

    def store(self, results, type: STORETYPE):
        if type == STORETYPE.CONFIGS:
            file_path = (
                f"{self.config.get("output_directory")}/{self.job_id}/configs.csv"
            )
        elif type == STORETYPE.EVALS:
            file_path = f"{self.config.get("output_directory")}/{self.job_id}/evals.csv"
        elif type == STORETYPE.SCORES:
            file_path = (
                f"{self.config.get("output_directory")}/{self.job_id}/scores.csv"
            )
        elif type == STORETYPE.SUMMARY:
            file_path = (
                f"{self.config.get("output_directory")}/{self.job_id}/summary.csv"
            )

        file_name = os.path.basename(file_path)
        directory = os.path.dirname(file_path)
        os.makedirs(directory, exist_ok=True)

        results.to_csv(file_path, index=False)
        logging.info(
            "Created csv {} for {} in directory {}".format(file_name, type, directory)
        )
