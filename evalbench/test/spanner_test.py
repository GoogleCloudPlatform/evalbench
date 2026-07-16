import os
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


@pytest.mark.skipif(os.environ.get("SKIP_CLOUD_TESTS") == "true", reason="Skipping cloud-dependent tests")
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

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_generated_column(self, client):
        # Create table with generated column
        create_table = "CREATE TABLE `ut_gen` (id INT64, a INT64, b INT64, gen INT64 AS (a + b) STORED) PRIMARY KEY (id)"
        client.batch_execute([create_table])
        try:
            # Data omitting the generated column (3 values)
            data = {"ut_gen": [[1, 10, 20], [2, 100, 200]]}
            client.insert_data(data)
            
            # Verify data
            res = client.execute("SELECT id, a, b, gen FROM `ut_gen` ORDER BY id")
            assert len(res[0]) == 2
            assert res[0][0]["id"] == 1
            assert res[0][0]["gen"] == 30
            assert res[0][1]["id"] == 2
            assert res[0][1]["gen"] == 300
        finally:
            client.batch_execute(["DROP TABLE `ut_gen`"])

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_default_column_omitted(self, client):
        # Create table with default column
        create_table = "CREATE TABLE `ut_def` (id INT64, val INT64, def_val INT64 DEFAULT (42)) PRIMARY KEY (id)"
        client.batch_execute([create_table])
        try:
            # Data omitting default column (2 values)
            data = {"ut_def": [[1, 10], [2, 20]]}
            client.insert_data(data)
            
            # Verify default value is populated
            res = client.execute("SELECT id, val, def_val FROM `ut_def` ORDER BY id")
            assert len(res[0]) == 2
            assert res[0][0]["id"] == 1
            assert res[0][0]["def_val"] == 42
            assert res[0][1]["id"] == 2
            assert res[0][1]["def_val"] == 42
        finally:
            client.batch_execute(["DROP TABLE `ut_def`"])

    @pytest.mark.filterwarnings("ignore::DeprecationWarning")
    def test_default_column_included(self, client):
        # Create table with default column
        create_table = "CREATE TABLE `ut_def_inc` (id INT64, val INT64, def_val INT64 DEFAULT (42)) PRIMARY KEY (id)"
        client.batch_execute([create_table])
        try:
            # Data including default column (3 values)
            data = {"ut_def_inc": [[1, 10, 99], [2, 20, 100]]}
            client.insert_data(data)
            
            # Verify explicit value is populated
            res = client.execute("SELECT id, val, def_val FROM `ut_def_inc` ORDER BY id")
            assert len(res[0]) == 2
            assert res[0][0]["id"] == 1
            assert res[0][0]["def_val"] == 99
            assert res[0][1]["id"] == 2
            assert res[0][1]["def_val"] == 100
        finally:
            client.batch_execute(["DROP TABLE `ut_def_inc`"])
