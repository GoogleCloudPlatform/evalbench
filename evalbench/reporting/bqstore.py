from google.cloud import bigquery
import logging
from reporting.report import Reporter, STORETYPE
from util.gcp import get_gcp_project

_CHUNK_SIZE = 250


def _split_dataframe(df, chunk_size):
    """
    Splits a pandas DataFrame into chunks of a specified size.

    Args:
      df: The DataFrame to split.
      chunk_size: The desired size of each chunk.

    Yields:
      A generator that yields each chunk of the DataFrame.
    """
    num_chunks = len(df) // chunk_size + (len(df) % chunk_size > 0)
    for i in range(num_chunks):
        start = i * chunk_size
        end = (i + 1) * chunk_size  # Py/Pandas slicing handles not going out of bound
        yield df[start:end]


class BigQueryReporter(Reporter):
    def __init__(self, reporting_config, job_id, run_time):
        super().__init__(reporting_config, job_id, run_time)
        self.project_id = get_gcp_project(reporting_config.get("gcp_project_id"))
        self.location = reporting_config.get("dataset_location") or "US"
        self.dataset_id = "{}.evalbench".format(self.project_id)
        self.configs_table = "{}.configs".format(self.dataset_id)
        self.results_table = "{}.results".format(self.dataset_id)
        self.scores_table = "{}.scores".format(self.dataset_id)
        self.summary_table = "{}.summary".format(self.dataset_id)

    def store(self, results, type: STORETYPE):
        # Construct a BigQuery client object.
        client = bigquery.Client()
        # Construct a full Dataset object to send to the API.
        dataset = bigquery.Dataset(self.dataset_id)
        dataset.location = self.location
        dataset = client.create_dataset(dataset, exists_ok=True, timeout=30)
        logging.info(
            "Created dataset {}.{} for {}".format(
                client.project, dataset.dataset_id, type
            )
        )
        job_config = bigquery.LoadJobConfig()
        job_config.schema_update_options = [
            bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION,
            bigquery.SchemaUpdateOption.ALLOW_FIELD_RELAXATION,
        ]
        if type == STORETYPE.CONFIGS:
            table = self.configs_table
        elif type == STORETYPE.EVALS:
            table = self.results_table
        elif type == STORETYPE.SCORES:
            table = self.scores_table
        elif type == STORETYPE.SUMMARY:
            table = self.summary_table

        # Chunk this to avoid BQ OOM
        job_config.write_disposition = bigquery.job.WriteDisposition.WRITE_APPEND  # type: ignore
        for chunk in _split_dataframe(results, _CHUNK_SIZE):
            job = client.load_table_from_dataframe(chunk, table, job_config=job_config)
            job.result()  # Wait for the job to complete.
