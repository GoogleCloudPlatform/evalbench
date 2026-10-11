from unittest import mock

import pytest

from databases import bigquery as bq_module


def _db_config(**overrides):
    config = {
        "db_type": "bigquery",
        "database_name": "my_dataset",
        "database_path": "projects/my-project/datasets/my_dataset",
        "gcp_project_id": "my-project",
        "max_executions_per_minute": 10,
    }
    config.update(overrides)
    return config


@pytest.fixture
def mock_client_cls():
    with mock.patch.object(bq_module.bigquery, "Client") as client_cls:
        yield client_cls


def test_client_uses_configured_location(mock_client_cls):
    db = bq_module.BQDB(_db_config(location="us-central1"))

    mock_client_cls.assert_called_once_with(
        project="my-project", location="us-central1"
    )
    assert db.location == "us-central1"


def test_client_defaults_to_us_location(mock_client_cls):
    bq_module.BQDB(_db_config())

    mock_client_cls.assert_called_once_with(project="my-project", location="US")
