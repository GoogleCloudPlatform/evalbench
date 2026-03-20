import logging
import re
import json
from typing import Any, List, Optional, Tuple
from .db import DB
from .util import DatabaseSchema
import pymongo
from pymongo import MongoClient

# Matches: db.<collection>.aggregate([...])  or  db.<collection>.find({...})
_SHELL_QUERY_RE = re.compile(
    r'^db\.(\w+)\.(aggregate|find|countDocuments)\s*\(', re.DOTALL
)


class MongoDB(DB):
    def __init__(self, db_config):
        super().__init__(db_config)

        self.connection_string = db_config.get("connection_string")

        # Handle DB name mismatch: replace underscores with hyphens
        if "_" in self.db_name:
            self.db_name = self.db_name.replace("_", "-")

        self.client = MongoClient(
            self.connection_string, tlsAllowInvalidCertificates=True
        )
        self.db = self.client[self.db_name]

    def ensure_database_exists(self, database_name: str) -> None:
        pass

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

    # ------------------------------------------------------------------
    # Shell-style query: db.<collection>.aggregate([...])
    # ------------------------------------------------------------------
    def _execute_shell_query(self, query_str: str) -> Tuple[List, Optional[str]]:
        try:
            m = _SHELL_QUERY_RE.match(query_str)
            if not m:
                return [], f"Unsupported shell query format: {query_str}"

            collection = m.group(1)
            method = m.group(2)

            # Extract args between the outermost ( and last )
            open_paren = query_str.index("(")
            close_paren = query_str.rindex(")")
            args_str = query_str[open_paren + 1: close_paren].strip()

            if method == "aggregate":
                pipeline = json.loads(args_str)
                cursor = self.db[collection].aggregate(pipeline)
                return list(cursor), None

            elif method == "find":
                # Support find(filter) and find(filter, projection)
                # Wrap in [] to parse as array, handles both cases cleanly
                args_list = json.loads(f"[{args_str}]") if args_str else [{}]
                filter_doc = args_list[0] if len(args_list) > 0 else {}
                projection = args_list[1] if len(args_list) > 1 else None
                cursor = self.db[collection].find(filter_doc, projection)
                return list(cursor), None

            elif method == "countDocuments":
                filter_doc = json.loads(args_str) if args_str else {}
                count = self.db[collection].count_documents(filter_doc)
                return [{"count": count}], None

        except json.JSONDecodeError as e:
            return [], f"Invalid JSON in shell query args: {e}"
        except Exception as e:
            return [], str(e)

        return [], f"Unsupported shell method: {method}"

    # ------------------------------------------------------------------
    # JSON dict-style query dispatcher
    # ------------------------------------------------------------------
    def _execute_query(self, query_str: str) -> Tuple[List, Optional[str]]:
        query_str = query_str.strip()

        # Dispatch shell-style queries (db.collection.method(...))
        if query_str.startswith("db."):
            return self._execute_shell_query(query_str)

        try:
            query_obj = json.loads(query_str)

            # Array of commands — execute each in sequence (used by setup.json)
            if isinstance(query_obj, list):
                for item in query_obj:
                    if isinstance(item, dict):
                        _, err = self._execute_query(json.dumps(item))
                        if err:
                            logging.warning(f"Error in batch command item: {err}")
                return [], None

            # {"find": "collection", "filter": {...}, "projection": {...}}
            if "find" in query_obj:
                collection_name = query_obj["find"]
                filter_doc = query_obj.get("filter", {})
                projection = query_obj.get("projection")
                limit = query_obj.get("limit", 0)
                cursor = self.db[collection_name].find(filter_doc, projection)
                if limit > 0:
                    cursor = cursor.limit(limit)
                return list(cursor), None

            # {"aggregate": "collection", "pipeline": [...]}
            elif "aggregate" in query_obj:
                collection_name = query_obj["aggregate"]
                pipeline = query_obj.get("pipeline", [])
                cursor = self.db[collection_name].aggregate(pipeline)
                return list(cursor), None

            # {"count": "collection", "filter": {...}}
            elif "count" in query_obj:
                collection_name = query_obj["count"]
                filter_doc = query_obj.get("filter", {})
                count = self.db[collection_name].count_documents(filter_doc)
                return [{"count": count}], None

            # {"insertMany": "collection", "documents": [...]}
            elif "insertMany" in query_obj:
                collection_name = query_obj["insertMany"]
                documents = query_obj.get("documents", [])
                if documents:
                    self.db[collection_name].insert_many(documents)
                return [], None

            # {"dropCollection": "collection"}
            elif "dropCollection" in query_obj:
                collection_name = query_obj["dropCollection"]
                try:
                    self.db[collection_name].drop()
                except Exception:
                    self.db[collection_name].delete_many({})
                return [], None

            else:
                return [], f"Unsupported query format: {query_str}"

        except json.JSONDecodeError:
            return [], f"Invalid JSON query: {query_str}"
        except Exception as e:
            return [], str(e)

    def _execute(
        self, query: str, eval_query: Optional[str] = None
    ) -> Tuple[Any, Any, Any]:
        result, error = self._execute_query(query)
        if error:
            return None, None, error

        eval_result = None
        if eval_query:
            eval_result, eval_error = self._execute_query(eval_query)
            if eval_error:
                return result, None, eval_error

        return result, eval_result, None

    # ------------------------------------------------------------------
    # Schema introspection — recursively describes nested fields
    # ------------------------------------------------------------------
    def _extract_fields(self, doc: dict, prefix: str = "") -> List[dict]:
        """Recursively extract field names and types from a document."""
        columns = []
        for key, value in doc.items():
            if key == "_id":
                continue
            field_name = f"{prefix}.{key}" if prefix else key
            if isinstance(value, dict):
                columns.append({"name": field_name, "type": "object"})
                columns.extend(self._extract_fields(value, field_name))
            elif isinstance(value, list):
                if value and isinstance(value[0], dict):
                    columns.append({"name": field_name, "type": "array<object>"})
                    columns.extend(self._extract_fields(value[0], f"{field_name}[]"))
                else:
                    elem_type = type(value[0]).__name__ if value else "unknown"
                    columns.append({"name": field_name, "type": f"array<{elem_type}>"})
            else:
                columns.append({"name": field_name, "type": type(value).__name__})
        return columns

    def get_metadata(self) -> dict:
        db_metadata = {}
        try:
            collection_names = self.db.list_collection_names()
            for name in collection_names:
                doc = self.db[name].find_one()
                if doc:
                    db_metadata[name] = self._extract_fields(doc)
                else:
                    db_metadata[name] = []
        except Exception as e:
            logging.error(f"Failed to get metadata: {e}")
        return db_metadata

    def generate_ddl(self, schema: DatabaseSchema) -> list[str]:
        ddl = []
        for table in schema.tables:
            ddl.append(f"Collection: {table.name}")
            if table.columns:
                col_descs = [f"{col.name} ({col.type})" for col in table.columns]
                ddl.append(f"  Fields: {', '.join(col_descs)}")
        return ddl

    def create_tmp_database(self, database_name: str):
        pass

    def drop_tmp_database(self, database_name: str):
        self.client.drop_database(database_name)

    def drop_all_tables(self):
        for name in self.db.list_collection_names():
            try:
                self.db[name].drop()
            except Exception:
                self.db[name].delete_many({})

    def insert_data(
        self, data: dict[str, List[str]], setup: Optional[List[str]] = None
    ):
        """Insert CSV-style flat data. For document-model data use insertMany commands in setup.json."""
        if not data:
            return

        schema_mapping = {}
        if setup:
            for item in setup:
                try:
                    if item.strip().startswith("{"):
                        parsed = json.loads(item)
                        # Only use as schema map if it's a plain dict of lists (not a command)
                        if parsed and not any(
                            k in parsed for k in ("insertMany", "find", "aggregate", "dropCollection")
                        ):
                            schema_mapping = parsed
                            break
                except Exception:
                    continue

        for collection_name, rows in data.items():
            if not rows:
                continue

            if collection_name in schema_mapping:
                headers = schema_mapping[collection_name]
                start_index = 0
            else:
                headers = rows[0]
                start_index = 1

            documents = []
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
        self.dql_user = self.username or "default"
        self.dml_user = self.username or "default"
        self.tmp_user_password = self.password or ""

    def delete_tmp_user(self, username: str):
        pass
