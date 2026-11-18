"""The BigQuery reporter."""

from unittest import mock

import pandas as pd
import pytest

from reporting import bqstore
from reporting.report import STORETYPE


@pytest.fixture
def reporter():
    """Returns a reporter with a fake BigQuery client."""
    with mock.patch.object(bqstore.bigquery, "Client"):
        yield bqstore.BigQueryReporter(
            {"gcp_project_id": "my-project"}, "job-1", None)


def _loads(reporter):
    """Returns the (frame, table) pair of each load job."""
    calls = reporter.client.load_table_from_dataframe.call_args_list
    return [(call.args[0], call.args[1]) for call in calls]


@pytest.mark.parametrize("store_type, table", [
    (STORETYPE.CONFIGS, "configs"),
    (STORETYPE.EVALS, "results"),
    (STORETYPE.SCORES, "scores"),
    (STORETYPE.SUMMARY, "summary"),
])
def test_store_writes_each_type_to_its_table(reporter, store_type, table):
    reporter.store(pd.DataFrame([{"job_id": "job-1"}]), store_type)
    [(_, loaded_table)] = _loads(reporter)
    assert loaded_table == f"my-project.evalbench.{table}"


def test_run_stats_go_to_the_run_stats_table(reporter):
    reporter.store_run_stats(pd.DataFrame(
        [{"scenario_id": "s1", "pass_rate": 0.5}]))
    [(frame, table)] = _loads(reporter)
    assert table == "my-project.evalbench.run_stats"
    assert frame.to_dict(orient="records") == [
        {"scenario_id": "s1", "pass_rate": 0.5}]
    # The pass rate stays a number, so a query can aggregate it.
    assert frame["pass_rate"].dtype == "float64"


def test_run_stats_column_names_are_valid_in_bigquery(reporter):
    reporter.store_run_stats(pd.DataFrame(
        [{"scenario_id": "s1", "my-scorer.v2_pass_rate": 1.0}]))
    [(frame, _)] = _loads(reporter)
    assert list(frame.columns) == ["scenario_id", "my_scorer_v2_pass_rate"]


def test_empty_run_stats_are_not_loaded(reporter):
    reporter.store_run_stats(pd.DataFrame())
    assert _loads(reporter) == []
