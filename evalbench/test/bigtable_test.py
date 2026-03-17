import pytest

from databases import get_database
from util import get_SessionManager


TABLE_ID = "unit_test_table"


@pytest.fixture(scope="session")
def client():
    """Creates a Bigtable client for testing."""
    db_config = {
        "gcp_project_id": "cloud-db-nl2sql",
        "db_type": "bigtable",
        "database_path": "",
        "instance_id": "evalbench",
        "table_name": TABLE_ID,
        "max_executions_per_minute": 100,
        "secret_manager_path": "",
    }
    db_name = "unit_test_db"
    client = get_database(db_config, db_name)
    yield client
    session_manager = get_SessionManager()
    session_manager.shutdown()
    client.close_connections()


class TestBigtable:
    """Test suite for the Bigtable database client."""

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_create_session(self, client):
        """Tests that a simple query can be executed."""
        result, _, error = client.execute("SELECT 1 AS one")
        assert error is None
        assert result is not None
        assert result[0]["one"] == 1

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_get_metadata(self, client):
        """Tests that metadata (column families) can be retrieved."""
        metadata = client.get_metadata()
        # Assumes the test table has at least one column family
        assert "unit_test_table" in metadata
        assert len(metadata["unit_test_table"]) > 0

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_execute_query(self, client):
        """Tests executing queries with and without eval_query."""
        # Test without eval_query
        result, eval_result, error = client.execute(
            query=f"SELECT * FROM {TABLE_ID} LIMIT 1")
        assert error is None
        assert result is not None
        assert eval_result is None

        # Test with eval_query
        result, eval_result, error = client.execute(
            query=f"SELECT * FROM {TABLE_ID} LIMIT 1",
            eval_query=f"SELECT * FROM {TABLE_ID} LIMIT 2"
        )
        assert error is None
        assert result is not None
        assert eval_result is not None

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_placeholder_functions(self, client):
        """Tests placeholder functions that are not applicable to Bigtable."""
        # These functions are expected to do nothing and not raise errors
        client.create_tmp_database("test_db")
        client.drop_tmp_database("test_db")
        client.drop_all_tables()
        client.create_tmp_users("dql_user", "dml_user", "password")
        client.delete_tmp_user("dql_user")
        assert client._execute_auto_commit("some_query") == (True, None)
