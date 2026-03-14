from google.cloud import bigquery
import logging
from reporting.report import Reporter, STORETYPE
from util.gcp import get_gcp_project
import urllib.parse

try:
    import google.colab  # type: ignore
    from IPython.display import display, HTML  # type: ignore

    _IN_COLAB = True
except ImportError:
    _IN_COLAB = False

_CHUNK_SIZE = 250

_REPORT_QUERY = "WITH all_runs_with_set_tag AS ( SELECT job_id, database, REPLACE(REPLACE(REPLACE(dialects, '[', ''),']',''),'\\'','') AS dialect, id, nl_prompt, trim(generated_sql) AS generated_sql, golden_sql AS golden_sqls, eval_query AS eval_sqls, CASE WHEN generated_error IS NOT NULL THEN generated_error ELSE generated_result END AS generated_result, CASE WHEN golden_error IS NOT NULL THEN golden_error ELSE golden_result END AS golden_result, eval_results AS generated_eval_result, golden_eval_results AS golden_eval_result, DATE(run_time) AS date_of_eval, FROM evalbench.results WHERE job_id = @eval_id ) SELECT *, comparator = @correctness_scorer AS is_correctness_score, '__PROJECT_ID__' AS project_id FROM all_runs_with_set_tag AS eval LEFT JOIN ( SELECT id, job_id, score, COALESCE(dialects[SAFE_OFFSET(0)],'') AS dialect, database, comparator, IFNULL(comparison_logs, '') AS comparison_logs FROM evalbench.scores ) AS scores USING (job_id, id, dialect, database) ORDER BY date_of_eval DESC;"


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
        # Py/Pandas slicing handles not going out of bound
        end = (i + 1) * chunk_size
        yield df[start:end]


class BigQueryReporter(Reporter):
    def __init__(self, reporting_config, job_id, run_time):
        super().__init__(reporting_config, job_id, run_time)
        reporting_config = reporting_config or {}
        self.project_id = get_gcp_project(
            reporting_config.get("gcp_project_id"))
        self.location = reporting_config.get("dataset_location") or "US"
        self.client = bigquery.Client(project=self.project_id)
        self.dataset_id = "{}.evalbench".format(self.project_id)
        self.configs_table = "{}.configs".format(self.dataset_id)
        self.results_table = "{}.results".format(self.dataset_id)
        self.scores_table = "{}.scores".format(self.dataset_id)
        self.summary_table = "{}.summary".format(self.dataset_id)

    def store(self, results, type: STORETYPE):
        dataset = bigquery.Dataset(self.dataset_id)
        dataset.location = self.location
        dataset = self.client.create_dataset(
            dataset, exists_ok=True, timeout=30)
        logging.info(
            "Created dataset {}.{} for {}".format(
                self.client.project, dataset.dataset_id, type
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
            job = self.client.load_table_from_dataframe(
                chunk, table, job_config=job_config
            )
            job.result()  # Wait for the job to complete.

    def print_dashboard_links(self):
        report_date = self.run_time.strftime("%Y-%m-%d")
        report_name = f"{report_date} Evalbench Report (eval_id={self.job_id})"
        report_params = "{" + f'"eval_results.eval_id": "{self.job_id}"' + "}"
        report_query = _REPORT_QUERY.replace("__PROJECT_ID__", self.project_id)
        report_link = (
            "https://lookerstudio.google.com/reporting/create?"
            + "c.reportId=e7d7fc00-4268-45d6-b17b-160ca271a4d0"
            + "&ds.eval_results.connector=bigQuery"
            + "&ds.eval_results.type=CUSTOM_QUERY"
            + f"&ds.eval_results.projectId={urllib.parse.quote(self.project_id)}"
            + f"&ds.eval_results.sql={urllib.parse.quote(report_query)}"
            + f"&ds.eval_results.billingProjectId={urllib.parse.quote(self.project_id)}"
            + f"&r.reportName={urllib.parse.quote(report_name)}"
            + f"&params={urllib.parse.quote(report_params)}"
        )
        if _IN_COLAB:
            html_link = f'The evaluation report is now available on this <a href="{report_link}">Dashboard!</a>'
            display(HTML(html_link))  # type: ignore
        else:
            print(
                f"Results available at:\n\033[1;34m{report_link}\033[0m\n---\n")
