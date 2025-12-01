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

        self.client = MongoClient(self.connection_string)
        self.db = self.client[self.db_name]

    def close_connections(self):
        self.client.close()

    def batch_execute(self, commands: list[str]):
        # MongoDB doesn't support SQL batch execution in the same way.
        # We could implement bulk writes if commands were JSON, but for now we'll execute one by one.
        for command in commands:
            _, _, error = self.execute(command)
            if error:
                raise RuntimeError(f"{error}")

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
            
            # Basic support for 'find', 'aggregate', 'count_documents'
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
        # Return list of collections
        db_metadata = {}
        try:
            collection_names = self.db.list_collection_names()
            for name in collection_names:
                # For now let's return empty columns since MongoDB is schemaless. The schema could be 
                # inferred from the documents, but that's not implemented yet.
                db_metadata[name] = [] 
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
            # If we had columns, we could list them too
            # for col in table.columns:
            #     ddl.append(f"  - {col.name} ({col.type})")
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
            self.db.drop_collection(name)

    def insert_data(
        self, data: dict[str, List[str]], setup: Optional[List[str]] = None
    ):
        if not data:
            return
            
        for collection_name, rows in data.items():
            documents = []
            for row in rows:
                if isinstance(row, str):
                    try:
                        documents.append(json.loads(row))
                    except:
                        # Assume valid JSON for Mongo.
                        pass
                elif isinstance(row, dict):
                    documents.append(row)
            
            if documents:
                self.db[collection_name].insert_many(documents)

    def create_tmp_users(self, dql_user: str, dml_user: str, tmp_password: str):
        # Not implemented for now
        pass

    def delete_tmp_user(self, username: str):
        # Not implemented for now
        pass
