from enum import Enum
import pandas as pd
import logging
from abc import ABC, abstractmethod

STORETYPE = Enum("StoreType", ["CONFIGS", "EVALS", "SCORES", "SUMMARY"])


class Reporter(ABC):
    def __init__(self, reporting_config, job_id, run_time):
        self.config = reporting_config
        self.job_id = job_id
        self.run_time = run_time

    @abstractmethod
    def store(self, results, type: STORETYPE):
        pass


def get_dataframe(results):
    results_df = pd.DataFrame.from_dict(results, dtype="string")
    logging.info("Total Prompts: %d.", len(results_df))
    return results_df


def quick_summary(results_df):
    prompt_generator_error_df = results_df["prompt_generator_error"].notnull()
    generated_error_df = results_df["generated_error"].notnull()
    sql_generator_error_df = results_df["sql_generator_error"].notnull()
    golden_error_df = results_df["golden_error"].notnull()

    logging.info("Prompt Errors: %d.", len(results_df[prompt_generator_error_df]))
    logging.info("SQLGen Errors: %d.", len(results_df[sql_generator_error_df]))
    logging.info("SQLExec Gen Errors: %d.", len(results_df[generated_error_df]))
    logging.info("Golden Errors: %d.", len(results_df[golden_error_df]))
