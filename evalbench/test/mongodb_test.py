import pytest
import json
import sys
import os

sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../..")))

from evalbench.databases import get_database
from evalbench.util import get_SessionManager
import mongomock


# Mocking the MongoClient to use mongomock
@pytest.fixture(scope="session")
def client():
    """Creates a MongoDB client for testing using mongomock."""
    db_config = {
        "db_type": "mongodb",
        "database_name": "unit_test_db",
        "database_path": "",
        "max_executions_per_minute": 100,
        # We can use a mock connection string or just rely on the class logic
        "connection_string": "mongodb://mock-host:27017",
    }

    # Directly use mongomock.MongoClient instead of patching
    # This avoids issues with where MongoClient is imported
    from evalbench.databases import mongodb

    # Create a mock client
    mock_client = mongomock.MongoClient("mongodb://mock-host:27017")

    # Monkey patch the MongoClient in the module
    original_client = mongodb.MongoClient
    mongodb.MongoClient = lambda *args, **kwargs: mock_client

    try:
        client = get_database(db_config, "unit_test_db")
        yield client
        client.close_connections()
    finally:
        # Restore original
        mongodb.MongoClient = original_client


class TestMongoDB:
    """Test suite for the MongoDB database client."""

    def test_insert_and_find(self, client):
        """Tests inserting data and querying it."""
        # Insert data
        data = {
            "users": [
                ["name", "age"],
                ["Alice", 30],
                ["Bob", 25],
            ]
        }
        client.insert_data(data)

        # Query data
        query = json.dumps({"find": "users", "filter": {"name": "Alice"}})
        result, _, error = client.execute(query)

        assert error is None
        assert len(result) == 1
        assert result[0]["name"] == "Alice"
        assert result[0]["age"] == 30

    def test_aggregate(self, client):
        """Tests aggregation query."""
        # Data already inserted in previous test (session scope fixture, but we might want to clean up)
        # For safety, let's insert again or assume persistence.
        # mongomock is in-memory, so it persists for the session if not cleared.

        query = json.dumps(
            {
                "aggregate": "users",
                "pipeline": [
                    {"$match": {"age": {"$gt": 20}}},
                    {"$count": "total_users"},
                ],
            }
        )

        result, _, error = client.execute(query)
        assert error is None
        assert len(result) == 1
        assert result[0]["total_users"] == 2

    def test_count(self, client):
        """Tests count query."""
        query = json.dumps({"count": "users"})
        result, _, error = client.execute(query)
        assert error is None
        assert result[0]["count"] == 2

    def test_invalid_json(self, client):
        """Tests handling of invalid JSON."""
        result, _, error = client.execute("{invalid_json}")
        assert error is not None
        assert "Invalid JSON" in error

    def test_metadata(self, client):
        """Tests metadata retrieval."""
        metadata = client.get_metadata()
        assert "users" in metadata
