import pytest

from databases import get_database
from util import get_SessionManager


@pytest.fixture(scope="session")
def client():
    print("creating spanner client")
    db_config = {
        "gcp_project_id": "cloud-db-nl2sql",
        "db_type": "spanner",
        "database_path": "projects/cloud-db-nl2sql/instances/evalbench/databases/unit_test",
        "instance_id": "evalbench",
        "database_name": "unit_test",
        "max_executions_per_minute": 100,
        "secret_manager_path": "",
    }
    db_name = "unit_test"  # Assuming db_name is the database_id
    client = get_database(db_config, db_name)
    yield client
    sesssionmanager = get_SessionManager()
    sesssionmanager.shutdown()
    client.close_connections()


class TestSpanner:

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_create_session(self, client):
        ret = client.execute(f"select 1 as one")
        assert ret[0][0]["one"] == 1

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_create_table(self, client):
        create_table = "CREATE TABLE `ut` (main INT64) PRIMARY KEY (main)"
        client.batch_execute([create_table])

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_get_metadata(self, client):
        metadata = client.get_metadata()
        assert "ut" in metadata

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_drop_table(self, client):
        create_table = "DROP TABLE `ut`"
        client.batch_execute([create_table])
