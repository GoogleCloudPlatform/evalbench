import logging
import os
import json
import contextlib
from typing import Any, List, Optional, Tuple
from dateutil.parser import parse as parse_date
from datetime import timezone

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

        client = spanner.Client(**client_kwargs)
        spanner_instance = client.instance(db_config["instance_id"])
        self.database = spanner_instance.database(db_config["database_name"])
        self.pool = spanner.FixedSizePool(size=4, default_timeout=10)
        self.pool.bind(self.database)

    def close_connections(self):
        if self.emulator_manager:
            self.emulator_manager.stop()

    def batch_execute(self, commands: list[str]):
        if not commands:
            return
        logging.info(
            f"Executing batch of {len(commands)} statements in Spanner...")
        try:
            op = self.database.update_ddl(commands)
            op.result(timeout=600)
        except Exception as e:
            logging.warning(
                f"update_ddl failed, trying individual execution: {e}")
            for stmt in commands:
                _, _, error = self.execute(stmt)
                if error:
                    raise RuntimeError(
                        f"Error in batch statement: {stmt}\nError: {error}")

    def execute(self, query, eval_query=None, use_cache=False, rollback=False):
        if query.strip() == "":
            return None, None, None
        return self._execute(query, eval_query, rollback)

    def _execute(self, query, eval_query=None, rollback=False):
        def _run_execute(query, eval_query=None, rollback=False):
            result, eval_result, error = [], [], None
            try:
                with self.database.snapshot() as snapshot:
                    res = snapshot.execute_sql(query)
                    rows = list(res)
                    fields = [f.name for f in res.fields] if res.fields else []
                    result = [dict(zip(fields, row)) for row in rows]

                    if eval_query:
                        res_eval = snapshot.execute_sql(eval_query)
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
                res = snapshot.execute_sql(query)
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
        pass

    def drop_tmp_database(self, database_name):
        pass

    def drop_all_tables(self):
        try:
            with self.database.snapshot() as snapshot:
                schema_name = 'public' if self.expected_dialect_str == "POSTGRESQL" else ''
                res = snapshot.execute_sql(
                    f"SELECT table_name FROM information_schema.tables WHERE table_schema = '{schema_name}' AND table_type = 'BASE TABLE'")
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
                res = snapshot.execute_sql(query)
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
