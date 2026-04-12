"""
PostgreSQL → MySQL streaming converter.

Streams data directly from PG to MySQL without writing intermediate CSV files.
Memory usage: O(batch_size × avg_row_size), regardless of table size.
Resume-safe: checks MySQL row counts before processing each table.

Key differences from the standard pg_to_mysql workflow:
  - No pandas, no CSV files
  - Schema conversion is deterministic (type map, no AI)
  - Data streaming uses psycopg2 named server-side cursors (truly low-memory)
  - Row values from psycopg2 are already typed Python objects → clean conversion
"""

import json
import logging
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, date, time as dt_time
from decimal import Decimal
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import psycopg2
import psycopg2.extras
import pymysql

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# PostgreSQL → MySQL type mapping
# ---------------------------------------------------------------------------

# Maps PG udt_name / data_type to MySQL column type string.
# Priority: udt_name (more specific) → data_type (fallback).
_PG_TYPE_MAP: Dict[str, str] = {
    # Integers
    'int2': 'SMALLINT',
    'int4': 'INT',
    'int8': 'BIGINT',
    'integer': 'INT',
    'smallint': 'SMALLINT',
    'bigint': 'BIGINT',
    'serial': 'INT',
    'bigserial': 'BIGINT',
    'smallserial': 'SMALLINT',

    # Floating point
    'float4': 'FLOAT',
    'float8': 'DOUBLE',
    'real': 'FLOAT',
    'double precision': 'DOUBLE',

    # Exact numeric — use TEXT to preserve full precision for large values
    # (blockchain amounts, financial figures can exceed DECIMAL(65,30))
    'numeric': 'TEXT',
    'decimal': 'TEXT',
    'money': 'TEXT',

    # Boolean
    'bool': 'TINYINT(1)',
    'boolean': 'TINYINT(1)',

    # Text — always TEXT/LONGTEXT, never VARCHAR (avoids Data too long errors)
    'text': 'LONGTEXT',
    'varchar': 'TEXT',
    'character varying': 'TEXT',
    'bpchar': 'TEXT',       # CHAR(n) internal name
    'character': 'TEXT',
    'name': 'TEXT',         # PG internal "name" type (63 bytes)

    # Date / time
    'timestamptz': 'DATETIME',
    'timestamp with time zone': 'DATETIME',
    'timestamp without time zone': 'DATETIME',
    'timestamp': 'DATETIME',
    'date': 'DATE',
    'timetz': 'TIME',
    'time with time zone': 'TIME',
    'time without time zone': 'TIME',
    'time': 'TIME',
    'interval': 'TEXT',

    # JSON — LONGTEXT is safer than MySQL JSON type (avoids strict validation)
    'json': 'LONGTEXT',
    'jsonb': 'LONGTEXT',

    # Binary
    'bytea': 'LONGBLOB',

    # UUID
    'uuid': 'VARCHAR(36)',

    # Network
    'inet': 'TEXT',
    'cidr': 'TEXT',
    'macaddr': 'TEXT',
    'macaddr8': 'TEXT',

    # Geometric / GIS — store as LONGTEXT (WKT can be very large)
    'point': 'TEXT',
    'line': 'TEXT',
    'lseg': 'TEXT',
    'box': 'TEXT',
    'path': 'TEXT',
    'polygon': 'TEXT',
    'circle': 'TEXT',
    'geometry': 'LONGTEXT',
    'geography': 'LONGTEXT',

    # Full-text search
    'tsvector': 'LONGTEXT',
    'tsquery': 'TEXT',

    # Bit strings
    'bit': 'TEXT',
    'varbit': 'TEXT',
    'bit varying': 'TEXT',

    # Object identifier
    'oid': 'BIGINT',

    # XML
    'xml': 'LONGTEXT',

    # Catch-all for PG array types (udt_name starts with '_')
    # Handled separately in _pg_col_to_mysql_type().
}


def _pg_col_to_mysql_type(udt_name: str, data_type: str, char_max_len: Optional[int]) -> str:
    """Return a MySQL column type string for a PG column."""
    udt = udt_name.lower() if udt_name else ''
    dtype = data_type.lower() if data_type else ''

    # PG array types: udt_name starts with '_'
    if udt.startswith('_'):
        return 'LONGTEXT'

    # Check udt_name first (more specific), then data_type
    mysql_type = _PG_TYPE_MAP.get(udt) or _PG_TYPE_MAP.get(dtype)

    if mysql_type:
        return mysql_type

    # character varying with explicit length — still use TEXT for safety
    if dtype in ('character varying', 'varchar', 'character', 'char'):
        return 'TEXT'

    # Unknown type — fall back to LONGTEXT
    logger.warning(f"Unknown PG type udt={udt!r} dtype={dtype!r}, using LONGTEXT")
    return 'LONGTEXT'


# ---------------------------------------------------------------------------
# Value conversion: psycopg2 Python objects → MySQL-compatible Python objects
# ---------------------------------------------------------------------------

def _convert_value(value, mysql_type: str):
    """Convert a psycopg2-returned Python value to a MySQL-compatible value.

    psycopg2 already parses PG types into Python objects:
      - NULL          → None         (pass through, pymysql handles it)
      - bool          → True/False   (convert to 1/0)
      - datetime      → datetime     (strip tzinfo if timezone-aware)
      - Decimal       → Decimal      (convert to str for TEXT targets)
      - dict/list     → dict/list    (json.dumps for LONGTEXT)
      - memoryview    → bytes        (bytea → LONGBLOB)
      - everything else → pass through
    """
    if value is None:
        return None

    if isinstance(value, bool):
        return 1 if value else 0

    if isinstance(value, datetime):
        if value.tzinfo is not None:
            # Strip timezone — MySQL DATETIME has no tz support
            value = value.replace(tzinfo=None)
        return value

    if isinstance(value, Decimal):
        # TEXT columns need a string; DECIMAL columns can take Decimal directly
        mt = mysql_type.upper()
        if mt in ('TEXT', 'LONGTEXT') or mt.startswith('LONGTEXT'):
            return str(value)
        return value  # pymysql handles Decimal for DECIMAL/NUMERIC columns

    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False)

    if isinstance(value, memoryview):
        return bytes(value)

    if isinstance(value, (date, dt_time)):
        return value  # pymysql handles Python date and time objects

    return value


# ---------------------------------------------------------------------------
# Main streaming converter class
# ---------------------------------------------------------------------------

class PGToMySQLStreaming:
    """Stream all tables from a PostgreSQL database into MySQL.

    Usage::

        streamer = PGToMySQLStreaming(
            pg={'host': '...', 'port': '5432', 'user': '...', 'password': '...'},
            mysql={'host': '127.0.0.1', 'port': 3306, 'user': '...', 'password': '...'},
        )
        streamer.run('my_database')
    """

    def __init__(
        self,
        pg: Dict,
        mysql: Dict,
        migration_dir: Optional[Path] = None,
    ):
        """
        Args:
            pg: Dict with keys: host, port, user, password.
                The database name is passed per-run, not here.
            mysql: Dict with keys: host, port, user, password.
                The target database name is the same as the source.
            migration_dir: Optional path to write schema + logs.
                           Created automatically if not given.
        """
        self.pg = pg
        self.mysql = mysql
        self.migration_dir = Path(migration_dir) if migration_dir else None

    # ------------------------------------------------------------------
    # Internal PG helpers
    # ------------------------------------------------------------------

    def _pg_connect(self, database: str):
        return psycopg2.connect(
            host=self.pg['host'],
            port=int(self.pg.get('port', 5432)),
            user=self.pg['user'],
            password=self.pg['password'],
            database=database,
        )

    def _mysql_connect(self, database: Optional[str] = None):
        cfg = dict(
            host=self.mysql['host'],
            port=int(self.mysql.get('port', 3306)),
            user=self.mysql['user'],
            password=self.mysql['password'],
            charset='utf8mb4',
            autocommit=False,
        )
        if database:
            cfg['database'] = database
        return pymysql.connect(**cfg)

    # ------------------------------------------------------------------
    # Schema introspection + DDL generation
    # ------------------------------------------------------------------

    def get_pg_tables(self, pg_conn) -> List[str]:
        """Return list of user tables in public schema, dependency-ordered."""
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT table_name
                FROM information_schema.tables
                WHERE table_schema = 'public'
                  AND table_type = 'BASE TABLE'
                ORDER BY table_name
            """)
            return [r[0] for r in cur.fetchall()]

    def get_pg_columns(self, pg_conn, table_name: str) -> List[Dict]:
        """Return column metadata for a PG table."""
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT
                    column_name,
                    data_type,
                    udt_name,
                    character_maximum_length,
                    numeric_precision,
                    numeric_scale,
                    is_nullable
                FROM information_schema.columns
                WHERE table_schema = 'public'
                  AND table_name = %s
                ORDER BY ordinal_position
            """, (table_name,))
            return [
                {
                    'name': r[0],
                    'data_type': r[1],
                    'udt_name': r[2],
                    'char_max_len': r[3],
                    'numeric_precision': r[4],
                    'numeric_scale': r[5],
                    'nullable': r[6] == 'YES',
                    'mysql_type': _pg_col_to_mysql_type(r[2], r[1], r[3]),
                }
                for r in cur.fetchall()
            ]

    def get_pg_primary_keys(self, pg_conn, table_name: str) -> List[str]:
        """Return primary key column names for a PG table."""
        with pg_conn.cursor() as cur:
            cur.execute("""
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a
                  ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indisprimary
                  AND i.indrelid = (
                      SELECT c.oid FROM pg_class c
                      JOIN pg_namespace n ON c.relnamespace = n.oid
                      WHERE c.relname = %s AND n.nspname = 'public'
                  )
            """, (table_name,))
            return [r[0] for r in cur.fetchall()]

    def build_mysql_ddl(
        self,
        table_name: str,
        columns: List[Dict],
        primary_keys: List[str],
    ) -> str:
        """Generate a MySQL CREATE TABLE statement."""
        col_defs = []
        pk_set = set(primary_keys)

        for col in columns:
            mysql_type = col['mysql_type']
            not_null = ' NOT NULL' if col['name'] in pk_set else ''
            col_defs.append(f"  `{col['name']}` {mysql_type}{not_null}")

        if primary_keys:
            pk_cols = ', '.join(f'`{c}`' for c in primary_keys)
            col_defs.append(f"  PRIMARY KEY ({pk_cols})")

        body = ',\n'.join(col_defs)
        return (
            f"CREATE TABLE IF NOT EXISTS `{table_name}` (\n"
            f"{body}\n"
            f") ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;"
        )

    def setup_mysql_database(self, database: str, ddl_statements: List[str]):
        """Create MySQL database and apply all DDL statements."""
        conn = self._mysql_connect()
        try:
            with conn.cursor() as cur:
                cur.execute(
                    f"CREATE DATABASE IF NOT EXISTS `{database}` "
                    "CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci"
                )
                cur.execute(f"USE `{database}`")
                for ddl in ddl_statements:
                    cur.execute(ddl)
            conn.commit()
        finally:
            conn.close()

    # ------------------------------------------------------------------
    # Per-table streaming transfer
    # ------------------------------------------------------------------

    def _mysql_row_count(self, database: str, table_name: str) -> int:
        conn = self._mysql_connect(database)
        try:
            with conn.cursor() as cur:
                cur.execute(f"SELECT COUNT(*) FROM `{table_name}`")
                return cur.fetchone()[0]
        finally:
            conn.close()

    def stream_table(
        self,
        pg_database: str,
        mysql_database: str,
        table_name: str,
        columns: List[Dict],
        batch_size: int = 1000,
    ) -> int:
        """Stream one table from PG into MySQL.

        Uses a psycopg2 named server-side cursor so PG sends rows in batches
        (not all at once), keeping Python memory usage at O(batch_size).

        Returns:
            Number of rows inserted.
        """
        pg_conn = self._pg_connect(pg_database)
        mysql_conn = self._mysql_connect(mysql_database)
        total = 0
        t0 = time.time()

        col_names = [c['name'] for c in columns]
        mysql_types = {c['name']: c['mysql_type'] for c in columns}

        placeholders = ', '.join(['%s'] * len(col_names))
        col_list = ', '.join(f'`{c}`' for c in col_names)
        insert_sql = (
            f"INSERT INTO `{table_name}` ({col_list}) VALUES ({placeholders})"
        )

        try:
            # Named server-side cursor: PG streams rows in chunks, not all at once
            with pg_conn.cursor(f'stream_{table_name}') as pg_cur:
                pg_cur.itersize = batch_size
                pg_cur.execute(f'SELECT * FROM "{table_name}"')

                with mysql_conn.cursor() as my_cur:
                    batch = []
                    last_log = t0

                    for pg_row in pg_cur:
                        converted = tuple(
                            _convert_value(v, mysql_types[col_names[i]])
                            for i, v in enumerate(pg_row)
                        )
                        batch.append(converted)

                        if len(batch) >= batch_size:
                            my_cur.executemany(insert_sql, batch)
                            mysql_conn.commit()
                            total += len(batch)
                            batch = []

                            now = time.time()
                            if now - last_log >= 60:
                                elapsed = now - t0
                                logger.info(
                                    f"  {table_name}: {total:,} rows "
                                    f"in {elapsed:.0f}s ({total/elapsed:.0f} rows/s)"
                                )
                                last_log = now

                    if batch:
                        my_cur.executemany(insert_sql, batch)
                        mysql_conn.commit()
                        total += len(batch)

        finally:
            pg_conn.close()
            mysql_conn.close()

        elapsed = time.time() - t0
        logger.info(
            f"  [DONE] {table_name}: {total:,} rows in {elapsed:.0f}s "
            f"({total/elapsed:.0f} rows/s)" if elapsed > 0 else
            f"  [DONE] {table_name}: {total:,} rows"
        )
        return total

    # ------------------------------------------------------------------
    # Main entry point
    # ------------------------------------------------------------------

    def run(
        self,
        database: str,
        tables: Optional[List[str]] = None,
        batch_size: int = 1000,
        workers: int = 1,
        skip_schema: bool = False,
    ) -> Dict[str, int]:
        """Run the full streaming migration for one database.

        Args:
            database: PG source database name (same name used for MySQL target).
            tables: Subset of tables to process. If None, all tables are processed.
            batch_size: Rows per INSERT batch.
            workers: Number of parallel table workers (each gets its own connections).
            skip_schema: If True, skip DDL creation (useful when resuming after schema
                         already exists).

        Returns:
            Dict mapping table_name → rows_inserted.
        """
        logger.info(f"=== PG → MySQL streaming: {database} ===")
        t_start = time.time()

        pg_conn = self._pg_connect(database)
        try:
            all_tables = self.get_pg_tables(pg_conn)
            target_tables = tables if tables else all_tables

            # Build column metadata for every target table
            table_meta: Dict[str, Tuple[List[Dict], List[str]]] = {}
            ddl_statements = []
            for tbl in target_tables:
                cols = self.get_pg_columns(pg_conn, tbl)
                pks = self.get_pg_primary_keys(pg_conn, tbl)
                table_meta[tbl] = (cols, pks)
                ddl_statements.append(self.build_mysql_ddl(tbl, cols, pks))
        finally:
            pg_conn.close()

        # Optionally save schema to migration_dir
        if self.migration_dir:
            self.migration_dir.mkdir(parents=True, exist_ok=True)
            schema_path = self.migration_dir / 'mysql_schema.sql'
            schema_path.write_text('\n\n'.join(ddl_statements), encoding='utf-8')
            logger.info(f"MySQL schema written to {schema_path}")

        # Create MySQL database + tables
        if not skip_schema:
            logger.info("Setting up MySQL database and tables...")
            self.setup_mysql_database(database, ddl_statements)
            logger.info("  Done.")

        # Stream each table (parallel if workers > 1)
        results: Dict[str, int] = {}

        def _process_table(tbl: str) -> Tuple[str, int]:
            cols, _ = table_meta[tbl]
            existing = self._mysql_row_count(database, tbl)
            if existing > 0:
                logger.info(f"  [SKIP] {tbl}: already has {existing:,} rows")
                return tbl, existing

            logger.info(f"  [START] {tbl} ({len(cols)} columns)...")
            rows = self.stream_table(database, database, tbl, cols, batch_size)
            return tbl, rows

        if workers <= 1:
            for tbl in target_tables:
                name, rows = _process_table(tbl)
                results[name] = rows
        else:
            with ThreadPoolExecutor(max_workers=workers) as ex:
                futures = {ex.submit(_process_table, tbl): tbl for tbl in target_tables}
                for fut in as_completed(futures):
                    exc = fut.exception()
                    if exc:
                        tbl = futures[fut]
                        logger.error(f"  [ERROR] {tbl}: {exc}")
                        raise exc
                    name, rows = fut.result()
                    results[name] = rows

        total_rows = sum(results.values())
        elapsed = time.time() - t_start
        logger.info(
            f"\n=== Done: {database} — {total_rows:,} total rows "
            f"across {len(results)} tables in {elapsed:.0f}s ==="
        )

        # Write SUCCESS marker if migration_dir is set
        if self.migration_dir:
            (self.migration_dir / 'SUCCESS').touch()

        return results
