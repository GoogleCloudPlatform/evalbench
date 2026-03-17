import logging
import os
import json
import contextlib
import time
from decimal import Decimal
from typing import Any, List, Optional, Tuple
from dateutil.parser import parse as parse_date
from datetime import timezone

from google.api_core import exceptions
from google.cloud import spanner
from google.cloud.spanner_admin_database_v1.types import DatabaseDialect

from .db import DB
from .util import (
    get_db_secret,
    with_cache_execute,
    DatabaseSchema,
    Table,
    Column
)
from util.rate_limit import rate_limit, ResourceExhaustedError
from .emulator_manager import SpannerEmulatorManager


class SpannerDB(DB):
    def __init__(self, db_config):
        super().__init__(db_config)
        self.config = db_config
        self.dialect = db_config.get("dialect", "spanner_gsql")
        self.db_type = "spanner"
        self.engine = None

        self.emulator_manager = None
        self.use_managed_emulator = db_config.get(
            "use_managed_emulator", False)

        raw_dialect = self.dialect.lower()
        logging.debug(f"SpannerDB init for {db_config.get('database_name')} with self.dialect={self.dialect}")
        if "pg" in raw_dialect or "postgres" in raw_dialect:
            self.dialect_enum = DatabaseDialect.POSTGRESQL
            self.expected_dialect_str = "POSTGRESQL"
        else:
            self.dialect_enum = DatabaseDialect.GOOGLE_STANDARD_SQL
            self.expected_dialect_str = "GOOGLESQL"

        client_kwargs = {"project": db_config["gcp_project_id"]}
        if self.use_managed_emulator:
            self.emulator_manager = SpannerEmulatorManager()
            self.emulator_manager.start()
            client_kwargs.update(self.emulator_manager.get_client_config(
                db_config["gcp_project_id"]))
            self.emulator_manager.provision_database(
                db_config["gcp_project_id"],
                db_config["instance_id"],
                db_config["database_name"],
                dialect=self.expected_dialect_str)
        elif not os.environ.get("SPANNER_EMULATOR_HOST"):
            client_kwargs["client_options"] = {
                "api_endpoint": "spanner.googleapis.com"}

        self.project_id = db_config["gcp_project_id"]
        self.instance_id = db_config["instance_id"]
        # Spanner database IDs cannot end with an underscore
        db_name = db_config["database_name"].rstrip("_")
        client_kwargs["disable_builtin_metrics"] = True
        client = spanner.Client(**client_kwargs)
        self.spanner_instance = client.instance(self.instance_id)
        self.database = self.spanner_instance.database(db_name, database_dialect=self.dialect_enum)

    def close_connections(self):
        if self.emulator_manager:
            self.emulator_manager.stop()

    def batch_execute(self, commands: list[str]):
        if not commands:
            return
        logging.debug(f"Executing batch in {self.database.database_id}. Object dialect: {self.database.database_dialect}")
        logging.debug(f"Executing batch of {len(commands)} statements in Spanner {self.expected_dialect_str} for {self.database.database_id}")
        if commands:
            logging.debug(f"First statement: {commands[0][:100]}...")
        try:
            op = self.database.update_ddl(commands)
            op.result(timeout=600)
        except Exception as e:
            logging.warning(
                f"update_ddl failed, trying individual execution: {e}")
            for stmt in commands:
                _, _, error = self.execute(stmt)
                if error:
                    # Ignore 'already exists' / 'Duplicate name' errors during fallback
                    if "Duplicate name" in error or "already exists" in error:
                        logging.info(f"Ignoring duplicate error during fallback: {error}")
                    else:
                        raise RuntimeError(
                            f"Error in batch statement: {stmt}\nError: {error}")

    def execute(self, query, eval_query=None, use_cache=False, rollback=False):
        if query.strip() == "":
            return None, None, None

        # Detect DDL
        upper_query = query.strip().upper()
        is_ddl = any(upper_query.startswith(prefix) for prefix in ["CREATE", "ALTER", "DROP", "RENAME"])

        if is_ddl:
            logging.info(f"Executing DDL in Spanner: {query[:100]}...")
            try:
                op = self.database.update_ddl([query])
                op.result(timeout=600)
                return [{"status": "success"}], None, None
            except Exception as e:
                return None, None, str(e)

        return self._execute(query, eval_query, rollback)

    def _execute(self, query, eval_query=None, rollback=False):

        # Detect INFORMATION_SCHEMA queries which cannot be run in RW transactions
        if "information_schema" in query.lower():
            rollback = False

        is_dml = any(query.strip().upper().startswith(prefix)
                     for prefix in ["INSERT", "UPDATE", "DELETE"])

        def _run_execute(query, eval_query=None, rollback=False):
            result, eval_result, error = [], [], None

            if is_dml or rollback:
                class RollbackException(Exception):
                    pass

                def _tx_logic(transaction):
                    nonlocal result, eval_result, error
                    try:
                        if is_dml:
                            rows_affected = transaction.execute_update(
                                query, timeout=15)
                            result = [{"rows_affected": rows_affected}]
                        else:
                            res = transaction.execute_sql(query, timeout=15)
                            rows = list(res)
                            fields = [
                                f.name for f in res.fields] if res.fields else []
                            result = [dict(zip(fields, row)) for row in rows]

                        if eval_query:
                            res_eval = transaction.execute_sql(
                                eval_query, timeout=15)
                            rows_eval = list(res_eval)
                            fields_eval = [
                                f.name for f in res_eval.fields] if res_eval.fields else []
                            eval_result = [dict(zip(fields_eval, row))
                                           for row in rows_eval]
                    except Exception as e:
                        error = str(e)
                    raise RollbackException()

                try:
                    self.database.run_in_transaction(_tx_logic)
                except RollbackException:
                    pass
                except Exception as e:
                    if not error:
                        error = str(e)
            else:
                try:
                    with self.database.snapshot() as snapshot:
                        res = snapshot.execute_sql(query, timeout=15)
                        rows = list(res)
                        fields = [
                            f.name for f in res.fields] if res.fields else []
                        result = [dict(zip(fields, row)) for row in rows]

                        if eval_query:
                            res_eval = snapshot.execute_sql(
                                eval_query, timeout=15)
                            rows_eval = list(res_eval)
                            fields_eval = [
                                f.name for f in res_eval.fields] if res_eval.fields else []
                            eval_result = [dict(zip(fields_eval, row))
                                           for row in rows_eval]
                except Exception as e:
                    error = str(e)
            return result, eval_result, error

        try:
            return rate_limit(
                (query,
                 eval_query,
                 rollback),
                _run_execute,
                self.execs_per_minute,
                self.semaphore,
                self.max_attempts)
        except ResourceExhaustedError:
            return None, None, None

    def get_metadata(self):
        db_metadata = {}
        try:
            schema_name = 'public' if self.expected_dialect_str == "POSTGRESQL" else ''
            type_col = "spanner_type" if self.expected_dialect_str == "GOOGLESQL" else "data_type"
            query = f"SELECT table_name, column_name, {type_col} FROM information_schema.columns WHERE table_schema = '{schema_name}' ORDER BY table_name, ordinal_position"
            with self.database.snapshot() as snapshot:
                res = snapshot.execute_sql(query, timeout=15)
                for row in res:
                    t_name, c_name, d_type = row[0], row[1], row[2]
                    if t_name not in db_metadata:
                        db_metadata[t_name] = []
                    db_metadata[t_name].append(
                        {"name": c_name, "type": str(d_type)})

            if db_metadata:
                logging.info(
                    f"Metadata extracted for {
                        len(db_metadata)} tables in Spanner {
                        self.expected_dialect_str}")
                return db_metadata
            else:
                logging.warning(
                    f"No metadata found in Spanner {
                        self.expected_dialect_str} information_schema for schema '{schema_name}'")
        except Exception as e:
            logging.error(f"Native metadata inspection failed: {e}")
        return db_metadata

    def generate_ddl(self, schema: DatabaseSchema) -> str:
        ddl_parts = []
        for table in schema.tables:
            cols = ", ".join([f"{c.name} {c.type}" for c in table.columns])
            ddl_parts.append(f"CREATE TABLE {table.name} (\n  {cols}\n);")
        return "\n\n".join(ddl_parts)

    def create_tmp_database(self, database_name):
        # Spanner database IDs cannot end with an underscore
        database_name = database_name.rstrip("_")
        logging.info(f"Creating temporary Spanner database: {database_name}...")
        self.ensure_database_exists(database_name)

    def drop_tmp_database(self, database_name):
        database_name = database_name.rstrip("_")
        logging.info(f"Dropping temporary Spanner database: {database_name}...")
        try:
            spanner_client = spanner.Client(disable_builtin_metrics=True)
            instance = spanner_client.instance(self.instance_id)
            database = instance.database(database_name)
            database.drop()
            logging.info(f"Successfully dropped Spanner database {database_name}.")
        except Exception as e:
            logging.warning(f"Failed to drop temporary Spanner database {database_name}: {e}")

    def resetup_database(self, force=False, setup_users=False) -> None:
        # For Spanner, we need to ensure the database exists before we can resetup it
        db_id = self.database.database_id.rstrip("_")

        if self.database.exists():
            # Verify the backend dialect matches what we expect
            self.database.reload()
            if self.database.database_dialect != self.dialect_enum:
                logging.warning(f"Database {db_id} exists but has wrong dialect ({self.database.database_dialect} != {self.dialect_enum}). Dropping it.")
                self.drop_tmp_database(db_id)
                # Wait for drop to complete (drop is usually fast, but just in case)
                time.sleep(2)

        if not self.database.exists():
            logging.info(f"Database {db_id} does not exist. Creating it before setup...")
            self.create_tmp_database(db_id)

        super().resetup_database(force=force, setup_users=setup_users)

    def ensure_database_exists(self, database_name: str) -> None:
        spanner_client = spanner.Client(disable_builtin_metrics=True)
        instance_id = self.instance_id
        instance = spanner_client.instance(instance_id)
        # Create database with the configured dialect
        database = instance.database(database_name, database_dialect=self.dialect_enum)
        try:
            op = database.create()
            op.result()  # Wait for completion
            logging.info(f"Successfully created Spanner database {database_name}.")
        except exceptions.AlreadyExists:
            pass
        except Exception as e:
            raise RuntimeError(
                f"Failed to create Spanner DB {database_name}: {e}") from e

    def ensure_database_exists(self, database_name: str) -> None:
        from google.cloud import spanner
        from google.api_core import exceptions
        with spanner.Client() as spanner_client:
            instance_id = self.db_path.split("/")[-1]
            instance = spanner_client.instance(instance_id)
            database = instance.database(database_name)
            try:
                op = database.create()
                op.result()  # Wait for completion
            except exceptions.AlreadyExists:
                pass
            except Exception as e:
                raise RuntimeError(
                    f"Failed to create Spanner DB {database_name}: {e}") from e

    def drop_all_tables(self):
        try:
            if not self.database.exists():
                logging.info(f"Database {self.database.database_id} does not exist. Skipping drop_all_tables.")
                return

            with self.database.snapshot() as snapshot:
                schema_name = 'public' if self.expected_dialect_str == "POSTGRESQL" else ''
                res = snapshot.execute_sql(f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema_name}' AND table_type = 'BASE TABLE'", timeout=15)
                table_names = [row[0] for row in res]
                if not table_names:
                    return
                pending_tables = table_names
                for _ in range(5):
                    if not pending_tables:
                        break
                    next_pending = []
                    quote = '"' if self.expected_dialect_str == "POSTGRESQL" else '`'
                    for t in pending_tables:
                        try:
                            op = self.database.update_ddl(
                                [f"DROP TABLE {quote}{t}{quote}"])
                            op.result(timeout=60)
                        except Exception:
                            next_pending.append(t)
                    pending_tables = next_pending
        except Exception:
            pass

    def insert_data(self, data, setup=None):
        if not data:
            return
        try:
            table_info = {}
            schema_name = 'public' if self.expected_dialect_str == "POSTGRESQL" else ''
            with self.database.snapshot() as snapshot:
                type_col = "spanner_type" if self.expected_dialect_str == "GOOGLESQL" else "data_type"
                query = f"SELECT table_name, column_name, {type_col} FROM information_schema.columns WHERE table_schema = '{schema_name}' ORDER BY table_name, ordinal_position"
                res = snapshot.execute_sql(query, timeout=15)
                for row in res:
                    t_name, c_name, d_type = row[0], row[1], row[2]
                    if t_name not in table_info:
                        table_info[t_name] = {
                            "columns": [],
                            "json_indices": [],
                            "timestamp_indices": [],
                            "date_indices": [],
                            "int_indices": [],
                            "float_indices": [],
                            "numeric_indices": [],
                            "bool_indices": []}
                    idx = len(table_info[t_name]["columns"])
                    table_info[t_name]["columns"].append(c_name)
                    if d_type:
                        dt = d_type.lower()
                        if "json" in dt:
                            table_info[t_name]["json_indices"].append(idx)
                        elif "timestamp" in dt:
                            table_info[t_name]["timestamp_indices"].append(idx)
                        elif "date" in dt:
                            table_info[t_name]["date_indices"].append(idx)
                        elif "int" in dt:
                            table_info[t_name]["int_indices"].append(idx)
                        elif "numeric" in dt:
                            table_info[t_name]["numeric_indices"].append(idx)
                        elif "float" in dt or "double" in dt:
                            table_info[t_name]["float_indices"].append(idx)
                        elif "bool" in dt:
                            table_info[t_name]["bool_indices"].append(idx)

            for table_name, rows in data.items():
                info = table_info.get(table_name)
                if not info:
                    for k, v in table_info.items():
                        if k.lower() == table_name.lower():
                            info = v
                            break
                if not info:
                    continue
                columns = info["columns"]
                processed_rows = []
                for row in rows:
                    p_row = list(row)
                    for i in range(len(p_row)):
                        v = p_row[i]
                        if isinstance(v, str):
                            vs = v.strip()
                            if vs.startswith("'") and vs.endswith("'"):
                                vs = vs[1:-1]
                            if vs.lower() in ("", "null"):
                                p_row[i] = None
                            else:
                                p_row[i] = vs
                    for idx in info["int_indices"]:
                        if idx < len(p_row) and p_row[idx]:
                            try:
                                p_row[idx] = int(p_row[idx])
                            except BaseException:
                                pass
                    for idx in info["float_indices"]:
                        if idx < len(p_row) and p_row[idx]:
                            try:
                                p_row[idx] = float(p_row[idx])
                            except BaseException:
                                pass
                    for idx in info["numeric_indices"]:
                        if idx < len(p_row) and p_row[idx]:
                            try:
                                p_row[idx] = Decimal(str(p_row[idx]))
                            except BaseException:
                                pass
                    for idx in info["bool_indices"]:
                        if idx < len(p_row) and p_row[idx]:
                            bs = str(p_row[idx]).lower()
                            p_row[idx] = bs in ('t', 'true', '1', 'yes')
                    for idx in info["timestamp_indices"]:
                        if idx < len(p_row) and p_row[idx]:
                            try:
                                dt = parse_date(p_row[idx])
                                if not dt.tzinfo:
                                    dt = dt.replace(tzinfo=timezone.utc)
                                p_row[idx] = dt
                            except BaseException:
                                pass
                    for idx in info["date_indices"]:
                        if idx < len(p_row) and p_row[idx]:
                            try:
                                p_row[idx] = parse_date(p_row[idx]).date()
                            except BaseException:
                                pass
                    for idx in info["json_indices"]:
                        if idx < len(p_row) and p_row[idx]:
                            try:
                                parsed = json.loads(p_row[idx])
                                p_row[idx] = json.dumps(
                                    parsed) if self.expected_dialect_str == "POSTGRESQL" else spanner.Json(parsed)
                            except BaseException:
                                pass
                    processed_rows.append(p_row)

                batch_size = 500
                for i in range(0, len(processed_rows), batch_size):
                    batch = processed_rows[i:i + batch_size]
                    with self.database.batch() as b:
                        b.insert(table=table_name,
                                 columns=columns, values=batch)
        except Exception as e:
            raise RuntimeError(f"Could not insert data into Spanner: {e}")

    def create_tmp_users(self, dql_user, dml_user, tmp_password):
        pass

    def delete_tmp_user(self, username):
        pass
