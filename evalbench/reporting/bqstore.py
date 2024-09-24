from google.cloud import bigquery

import logging
from enum import Enum

STORETYPE = Enum('StoreType', ['CONFIGS', 'EVALS', 'SCORES', "SUMMARY"])
_CHUNK_SIZE = 20

project_id = "cloud-db-nl2sql"
dataset_id = "{}.evalbench".format(project_id)
configs_table = "{}.configs".format(dataset_id)
results_table = "{}.results".format(dataset_id)
scores_table = "{}.scores".format(dataset_id)
summary_table = "{}.summary".format(dataset_id)


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


def store(df, STORETYPE):
    # Construct a BigQuery client object.
    client = bigquery.Client()
    # Construct a full Dataset object to send to the API.
    dataset = bigquery.Dataset(dataset_id)
    dataset.location = "US"
    dataset = client.create_dataset(dataset, exists_ok=True, timeout=30)
    logging.info("Created dataset {}.{}".format(client.project, dataset.dataset_id))
    job_config = bigquery.LoadJobConfig()
    job_config.schema_update_options = [
        bigquery.SchemaUpdateOption.ALLOW_FIELD_ADDITION,
        bigquery.SchemaUpdateOption.ALLOW_FIELD_RELAXATION,
    ]
    if STORETYPE == STORETYPE.CONFIGS:
        table = configs_table
    elif STORETYPE == STORETYPE.EVALS:
        table = results_table
    elif STORETYPE == STORETYPE.SCORES:
        table = scores_table
    elif STORETYPE == STORETYPE.SUMMARY:
        table = summary_table

    # Chunk this to avoid BQ OOM
    job_config.write_disposition = bigquery.job.WriteDisposition.WRITE_APPEND
    for chunk in _split_dataframe(df, _CHUNK_SIZE):
        job = client.load_table_from_dataframe(chunk, table, job_config=job_config)
        job.result()  # Wait for the job to complete.
