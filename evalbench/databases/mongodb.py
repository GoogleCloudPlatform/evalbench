import logging
import json
from typing import Any, List, Optional, Tuple
from .db import DB
from .util import DatabaseSchema
import pymongo
from pymongo import MongoClient


class MongoDB(DB):
    def __init__(self, db_config):
        super().__init__(db_config)

        # Connection string support
        self.connection_string = db_config.get("connection_string")

        # Handle DB name mismatch: replace underscores with hyphens
        if "_" in self.db_name:
            self.db_name = self.db_name.replace("_", "-")

        self.client = MongoClient(
            self.connection_string, tlsAllowInvalidCertificates=True
        )
        self.db = self.client[self.db_name]

    def close_connections(self):
        self.client.close()

    def batch_execute(self, commands: list[str]):
        for command in commands:
            self.execute(command)

    def execute(
        self,
        query: str,
        eval_query: Optional[str] = None,
        use_cache=False,
        rollback=False,
    ) -> Tuple[Any, Any, Any]:
        if query.strip() == "":
            return None, None, None

        return self._execute(query, eval_query)

    def _execute_query(self, query_str: str) -> Tuple[List, Optional[str]]:
        try:
            # Expecting query to be a JSON string
            # Format: {"collection": "name", "operation": "find", "args": {...}}
            # OR simplified: {"find": "collection", "filter": {...}}
            # Let's support a flexible JSON format.

            query_obj = json.loads(query_str)

            # Basic support for 'find', 'aggregate', 'count_documents', 'dropCollection'
            # We can expand this based on needs.

            # Example 1: {"find": "users", "filter": {"age": {"$gt": 20}}}
            if "find" in query_obj:
                collection_name = query_obj["find"]
                filter_doc = query_obj.get("filter", {})
                projection = query_obj.get("projection")
                limit = query_obj.get("limit", 0)

                cursor = self.db[collection_name].find(filter_doc, projection)
                if limit > 0:
                    cursor = cursor.limit(limit)

                return list(cursor), None

            # Example 2: {"aggregate": "users", "pipeline": [...]}
            elif "aggregate" in query_obj:
                collection_name = query_obj["aggregate"]
                pipeline = query_obj.get("pipeline", [])

                cursor = self.db[collection_name].aggregate(pipeline)
                return list(cursor), None

            # Example 3: {"count": "users", "filter": {...}}
            elif "count" in query_obj:
                collection_name = query_obj["count"]
                filter_doc = query_obj.get("filter", {})

                count = self.db[collection_name].count_documents(filter_doc)
                return [{"count": count}], None

            # Example 4: {"dropCollection": "users"}
            elif "dropCollection" in query_obj:
                collection_name = query_obj["dropCollection"]
                try:
                    self.db[collection_name].drop()
                except Exception:
                    # Fallback to use delete_many to delete all documents.
                    self.db[collection_name].delete_many({})
                return [], None

            # Fallback: return error for unknown format.
            else:
                return [], f"Unsupported query format: {query_str}"

        except json.JSONDecodeError:
            return [], f"Invalid JSON query: {query_str}"
        except Exception as e:
            return [], str(e)

    def _execute(
        self, query: str, eval_query: Optional[str] = None
    ) -> Tuple[Any, Any, Any]:
        # Execute main query
        result, error = self._execute_query(query)
        if error:
            return None, None, error

        # Execute eval query if present
        eval_result = None
        if eval_query:
            eval_result, eval_error = self._execute_query(eval_query)
            if eval_error:
                return result, None, eval_error

        return result, eval_result, None

    def get_metadata(self) -> dict:
        # Return list of collections and their inferred schema
        db_metadata = {}
        try:
            collection_names = self.db.list_collection_names()
            for name in collection_names:
                columns = []
                # Infer schema from the first document
                doc = self.db[name].find_one()
                if doc:
                    for key, value in doc.items():
                        # Skip internal MongoDB fields if desired, but usually _id is relevant
                        # Map Python types to string representations
                        col_type = type(value).__name__
                        columns.append({"name": key, "type": col_type})

                db_metadata[name] = columns
        except Exception as e:
            logging.error(f"Failed to get metadata: {e}")
        return db_metadata

    def generate_ddl(
        self,
        schema: DatabaseSchema,
    ) -> list[str]:
        # Return a simple schema description for MongoDB
        ddl = []
        for table in schema.tables:
            ddl.append(f"Collection: {table.name}")
            if table.columns:
                col_descs = [f"{col.name} ({col.type})" for col in table.columns]
                ddl.append(f"  Fields: {', '.join(col_descs)}")
        return ddl

    def create_tmp_database(self, database_name: str):
        # In Mongo, switching to a DB creates it when data is written.
        # We don't need explicit creation usually, but we can't really "create" empty one easily without data.
        pass

    def drop_tmp_database(self, database_name: str):
        self.client.drop_database(database_name)

    def drop_all_tables(self):
        # Drop all collections
        for name in self.db.list_collection_names():
            try:
                self.db[name].drop()
            except Exception:
                # Fallback to delete_many if drop is unsupported
                self.db[name].delete_many({})

    def insert_data(
        self, data: dict[str, List[str]], setup: Optional[List[str]] = None
    ):
        if not data:
            return

        # Check if setup contains JSON schema
        schema_mapping = {}
        if setup:
            for item in setup:
                try:
                    if item.strip().startswith("{"):
                        schema_mapping = json.loads(item)
                        break
                except Exception:
                    continue

        for collection_name, rows in data.items():
            if not rows:
                continue

            headers = []
            start_index = 0

            # Determine headers: use schema if available, else first row
            if collection_name in schema_mapping:
                headers = schema_mapping[collection_name]
                # If using schema, all rows are data (no header row in CSV)
                start_index = 0
            else:
                # Fallback to assuming first row is header
                headers = rows[0]
                start_index = 1

            documents = []

            # Iterate over the rows
            for row in rows[start_index:]:
                if len(row) != len(headers):
                    logging.warning(
                        f"Row length mismatch in {collection_name}: expected {len(headers)}, got {len(row)}"
                    )
                    continue

                doc = {}
                for i, header in enumerate(headers):
                    if i < len(row):
                        val = row[i]
                        # Strip single quotes if the value is wrapped in them (common in some CSV exports)
                        if (
                            isinstance(val, str)
                            and len(val) >= 2
                            and val.startswith("'")
                            and val.endswith("'")
                        ):
                            val = val[1:-1]
                        doc[header] = val
                documents.append(doc)

            if documents:
                self.db[collection_name].insert_many(documents)

    def create_tmp_users(self, dql_user: str, dml_user: str, tmp_password: str):
        # For this environment, we might not have permissions to create users.
        # We'll just reuse the current user/auth for "tmp" users to satisfy the interface.
        self.dql_user = self.username or "default"
        self.dml_user = self.username or "default"
        self.tmp_user_password = self.password or ""

    def delete_tmp_user(self, username: str):
        # Not implemented
        pass
