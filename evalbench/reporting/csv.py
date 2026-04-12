import logging
from reporting.report import Reporter, STORETYPE
import os
import sys


class CsvReporter(Reporter):
    def __init__(self, reporting_config, job_id, run_time):
        super().__init__(reporting_config, job_id, run_time)

        # If running via eval_server.py (gRPC), force output to shared volume
        if sys.argv[0].endswith("eval_server.py"):
            self.output_dir = "/tmp_session_files/results"
        else:
            self.output_dir = self.config.get("output_directory", "results")

    def store(self, results, type: STORETYPE):
        if type == STORETYPE.CONFIGS:
            file_path = (
                f"{self.output_dir}/{self.job_id}/configs.csv"
            )
        elif type == STORETYPE.EVALS:
            file_path = f"{self.output_dir}/{self.job_id}/evals.csv"
        elif type == STORETYPE.SCORES:
            file_path = (
                f"{self.output_dir}/{self.job_id}/scores.csv"
            )
        elif type == STORETYPE.SUMMARY:
            file_path = (
                f"{self.output_dir}/{self.job_id}/summary.csv"
            )

        file_name = os.path.basename(file_path)
        directory = os.path.dirname(file_path)
        os.makedirs(directory, exist_ok=True)

        results.to_csv(file_path, index=False)
        logging.info(
            "Created csv {} for {} in directory {}".format(
                file_name, type, directory)
        )
