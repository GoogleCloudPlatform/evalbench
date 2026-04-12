"""MySQL database importer."""

import csv
import sys
csv.field_size_limit(sys.maxsize)
import pymysql
from pathlib import Path
from typing import List, Dict
from .base import DatabaseImporter


class MySQLImporter(DatabaseImporter):
    """Import data into MySQL database."""
    
    def __init__(
        self,
        host: str,
        port: str,
        user: str,
        password: str,
        database: str
    ):
        """Initialize MySQL importer.
        
        Args:
            host: MySQL host
            port: MySQL port
            user: MySQL user
            password: MySQL password
            database: Database name
        """
        super().__init__()
        self.host = host
        self.port = int(port)
        self.user = user
        self.password = password
        self.database = database
    
    def _get_connection(self, database=True):
        """Get a MySQL database connection.
        
        Args:
            database: If True, connect to the specific database. If False, connect without database.
        """
        config = {
            'host': self.host,
            'port': int(self.port),
            'user': self.user,
            'password': self.password,
            'local_infile': True,
            'charset': 'utf8mb4',
            'client_flag': pymysql.constants.CLIENT.MULTI_STATEMENTS
        }
        if database:
            config['database'] = self.database
        return pymysql.connect(**config)
    
    def _ensure_database_exists(self):
        """Ensure the target database exists, create if it doesn't."""
        conn = self._get_connection(database=False)
        cursor = conn.cursor()
        try:
            cursor.execute(f"CREATE DATABASE IF NOT EXISTS `{self.database}`")
            conn.commit()
        finally:
            cursor.close()
            conn.close()
    
    def wipe_database(self):
        """Wipe all tables from the database."""
        print("\n" + "=" * 80)
        print("STEP: Wiping destination database")
        print("=" * 80)
        
        # Ensure database exists
        self._ensure_database_exists()
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # Get list of tables
            cursor.execute(
                "SELECT table_name FROM information_schema.tables WHERE table_schema = %s",
                (self.database,)
            )
            tables = [row[0] for row in cursor.fetchall()]
            
            if tables:
                print(f"Found {len(tables)} tables to drop: {', '.join(tables)}")
                
                # Disable foreign key checks
                cursor.execute("SET FOREIGN_KEY_CHECKS=0")
                
                # Drop all tables
                for table in tables:
                    cursor.execute(f"DROP TABLE IF EXISTS `{table}`")
                
                # Re-enable foreign key checks
                cursor.execute("SET FOREIGN_KEY_CHECKS=1")
                conn.commit()
                
                print("✓ Database wiped successfully")
            else:
                print("✓ Database is already empty")
        finally:
            cursor.close()
            conn.close()
    
    def create_schema(self, schema_file: Path):
        """Create database schema from SQL file.
        
        Args:
            schema_file: Path to schema SQL file
        """
        print("\n" + "=" * 80)
        print("STEP: Creating schema")
        print("=" * 80)
        
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # Read schema file
            with open(schema_file, 'r') as f:
                schema_sql = f.read()
            
            # Split into individual statements and execute
            # MySQL connector requires executing statements one at a time
            statements = []
            current_statement = []
            
            for line in schema_sql.split('\n'):
                line = line.strip()
                # Skip comments and empty lines
                if not line or line.startswith('--'):
                    continue
                current_statement.append(line)
                if line.endswith(';'):
                    statements.append(' '.join(current_statement))
                    current_statement = []
            
            # Execute each statement
            for statement in statements:
                if statement.strip():
                    cursor.execute(statement)
            
            conn.commit()
            print("✓ Schema created successfully")
        finally:
            cursor.close()
            conn.close()
    
    def load_data(self, table: str, csv_file: Path, resuming: bool = False):
        """Load data from CSV file into table.

        When resuming=True (same CSV artifacts, interrupted by connection loss):
          checks how many rows already exist in MySQL and skips that many rows
          at the start of the CSV to continue from where we left off.

        When resuming=False (fresh AI attempt): caller has already TRUNCATEd
          the table, so skip_rows will be 0 and we start from row 1.

        Args:
            table: Table name
            csv_file: Path to CSV file
            resuming: Whether to use row-level resume (skip already-imported rows)

        Raises:
            Exception: If data loading fails
        """
        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            cursor.execute("SET FOREIGN_KEY_CHECKS=0")

            # Fetch column types to avoid converting valid text like 'Infinity' to NULL
            cursor.execute(
                "SELECT COLUMN_NAME, DATA_TYPE FROM information_schema.COLUMNS "
                "WHERE TABLE_SCHEMA = %s AND TABLE_NAME = %s", 
                (self.database, table)
            )
            col_types = {row[0].lower(): row[1].lower() for row in cursor.fetchall()}
            numeric_types = {'float', 'double', 'decimal', 'numeric', 'real'}

            # Row-level resume: only skip rows when continuing an interrupted
            # import with the same CSV files (resuming=True).
            skip_rows = 0
            if resuming:
                try:
                    cursor.execute(f"SELECT COUNT(*) FROM `{table}`")
                    skip_rows = cursor.fetchone()[0]
                except Exception:
                    skip_rows = 0

            if skip_rows > 0:
                print(f"    ↩ Resuming {table}: {skip_rows:,} rows already in MySQL, skipping to row {skip_rows + 1}")

            # newline='' is required by csv module to correctly handle embedded
            # newlines in quoted fields and prevent stray \r from entering values
            with open(csv_file, 'r', encoding='utf-8', newline='') as f:
                reader = csv.reader(f)
                headers = next(reader)  # Skip header row

                placeholders = ', '.join(['%s'] * len(headers))
                columns = ', '.join([f'`{col}`' for col in headers])
                insert_sql = f"INSERT INTO `{table}` ({columns}) VALUES ({placeholders})"

                batch_size = 1000
                rows_inserted = 0
                row_num = 0
                batch = []

                # Use EXACT case-sensitive match for PG special float values.
                # PostgreSQL CSV exports them as 'NaN', 'Infinity', '-Infinity'.
                # Case-insensitive matching incorrectly catches names like "Nan".
                _PG_SPECIAL_FLOATS = {'NaN', 'Infinity', '-Infinity'}

                def _flush(rows, rn):
                    nonlocal rows_inserted
                    # Build a single multi-row INSERT for the whole batch.
                    # One SQL round-trip for 1000 rows → ~100-200x faster than
                    # individual execute() calls.
                    multi_placeholders = ', '.join(
                        f'({placeholders})' for _ in rows
                    )
                    multi_sql = f"INSERT INTO `{table}` ({columns}) VALUES {multi_placeholders}"
                    flat_params = [val for row in rows for val in row]
                    try:
                        cursor.execute(multi_sql, flat_params)
                        conn.commit()
                        rows_inserted += len(rows)
                    except Exception:
                        conn.rollback()
                        # Batch failed - retry row-by-row to find the exact bad row
                        # so the agent gets actionable error info.
                        start_rn = rn - len(rows) + 1
                        for i, single_row in enumerate(rows):
                            try:
                                cursor.execute(insert_sql, single_row)
                                conn.commit()
                                rows_inserted += 1
                            except Exception as row_exc:
                                conn.rollback()
                                bad_rn = start_rn + i
                                row_preview = dict(zip(headers, single_row))
                                raise Exception(
                                    f"Row {bad_rn} in table {table} failed: {row_exc}\n"
                                    f"Bad row data: {row_preview}"
                                )
                        # All single rows succeeded - batch failure was transient, continue

                n_cols = len(headers)

                for row_num, row in enumerate(reader, start=1):
                    # Skip rows already committed in a previous attempt
                    if row_num <= skip_rows:
                        continue

                    processed_row = []
                    for i in range(n_cols):
                        # Pad missing columns with None, ignore extra columns
                        val = row[i] if i < len(row) else ''
                        if val == '' or val is None:
                            processed_row.append(None)
                        elif isinstance(val, str):
                            cleaned = val.replace('\r', '')
                            col_name = headers[i].lower()
                            is_numeric = col_types.get(col_name) in numeric_types
                            
                            if is_numeric and cleaned in _PG_SPECIAL_FLOATS:
                                processed_row.append(None)
                            else:
                                processed_row.append(cleaned)
                        else:
                            processed_row.append(val)
                    batch.append(tuple(processed_row))

                    if len(batch) >= batch_size:
                        _flush(batch, row_num)
                        batch = []

                if batch:
                    _flush(batch, row_num)

        except Exception as e:
            raise Exception(f"Error loading data into {table}: {e}")
        finally:
            try:
                cursor.execute("SET FOREIGN_KEY_CHECKS=1")
            except Exception as fk_err:
                # Best-effort attempt to re-enable foreign key checks; failures are logged but ignored
                print(
                    f"Warning: failed to re-enable FOREIGN_KEY_CHECKS in MySQLImporter.load_data: {fk_err}",
                    file=sys.stderr,
                )
            cursor.close()
            conn.close()

    def get_table_dependencies(self) -> List[str]:
        """Get tables in dependency order based on foreign keys.
        
        Returns:
            List of table names in load order
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            # Get all tables
            cursor.execute("SHOW TABLES")
            all_tables = [row[0] for row in cursor.fetchall()]
            
            # Get foreign key relationships
            dependencies = {}  # table -> list of tables it depends on
            
            for table in all_tables:
                cursor.execute("""
                    SELECT REFERENCED_TABLE_NAME 
                    FROM information_schema.KEY_COLUMN_USAGE 
                    WHERE TABLE_SCHEMA = %s 
                    AND TABLE_NAME = %s 
                    AND REFERENCED_TABLE_NAME IS NOT NULL
                """, (self.database, table))
                
                refs = [row[0] for row in cursor.fetchall()]
                if refs:
                    dependencies[table] = list(set(refs))
            
            # Topological sort
            visited = set()
            ordered = []
            
            def visit(table):
                if table in visited:
                    return
                visited.add(table)
                if table in dependencies:
                    for dep in dependencies[table]:
                        visit(dep)
                if table in all_tables and table not in ordered:
                    ordered.append(table)
            
            for table in all_tables:
                visit(table)
            
            return ordered
        finally:
            cursor.close()
            conn.close()
    
    def load_csv_data(self, csv_dir: Path, tables: List[str] = None, resuming: bool = False):
        """Load CSV data into MySQL tables.

        Supports table-level checkpointing: after each table is successfully
        imported a marker file  <csv_dir>/.imported_<table>  is written.
        On retry runs the table is skipped if the marker exists.

        Args:
            csv_dir: Directory containing CSV files
            tables: List of tables in dependency order (if None, will determine automatically)
            resuming: True when continuing an interrupted import with the same
                      CSV artifacts (safe to skip already-imported rows via
                      row-level resume). False on a fresh AI attempt: any
                      partial table data is truncated before reloading to avoid
                      mixing rows from different conversion generations.
        """
        print("\n" + "=" * 80)
        print("STEP: Uploading CSV files to MySQL")
        print("=" * 80)

        csv_dir = Path(csv_dir)
        csv_files = {f.stem: f for f in csv_dir.glob('*.csv')}

        if not csv_files:
            raise Exception(f"No CSV files found in {csv_dir}")

        # Get table load order if not provided
        if tables is None:
            tables = self.get_table_dependencies()

        print(f"Loading {len(csv_files)} tables in dependency order: {', '.join(tables)}")

        conn = self._get_connection()
        cursor = conn.cursor()

        try:
            for table_name in tables:
                if table_name not in csv_files:
                    print(f"  ⚠ Skipping {table_name} (no CSV file)")
                    continue

                # Table-level checkpoint: skip if already imported in a previous attempt
                checkpoint = csv_dir / f".imported_{table_name}"
                if checkpoint.exists():
                    print(f"  ✓ {table_name} already imported (checkpoint found), skipping")
                    continue

                csv_file = csv_files[table_name]
                print(f"  Loading {table_name}...")

                # When NOT resuming (fresh AI attempt), truncate any partial
                # data so we don't mix rows from different conversion generations.
                if not resuming:
                    try:
                        cursor.execute(f"SET FOREIGN_KEY_CHECKS=0")
                        cursor.execute(f"TRUNCATE TABLE `{table_name}`")
                        cursor.execute(f"SET FOREIGN_KEY_CHECKS=1")
                        conn.commit()
                    except Exception as e:
                        # Best-effort cleanup only: log and continue with import.
                        print(f"  ⚠ Failed to truncate {table_name} before import: {e}")

                self.load_data(table_name, csv_file, resuming=resuming)

                # Count rows
                cursor.close()
                conn.close()
                conn = self._get_connection()
                cursor = conn.cursor()

                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
                row_count = cursor.fetchone()[0]
                print(f"    ✓ Uploaded {row_count} rows")

                # Write checkpoint marker so this table is skipped on retry
                checkpoint.write_text(f"{row_count}\n")
        finally:
            cursor.close()
            conn.close()
    
    def verify_row_counts(self, expected_counts: Dict[str, int]) -> bool:
        """Verify row counts match expected values.
        
        Args:
            expected_counts: Dict mapping table names to expected row counts
            
        Returns:
            True if all counts match, False otherwise
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        try:
            all_match = True
            for table_name, expected in expected_counts.items():
                cursor.execute(f"SELECT COUNT(*) FROM `{table_name}`")
                actual = cursor.fetchone()[0]
                
                if actual != expected:
                    print(f"✗ Row count mismatch for {table_name}: expected {expected}, got {actual}")
                    all_match = False
                else:
                    print(f"✓ Row count match for {table_name}: {actual}")
            
            return all_match
        finally:
            cursor.close()
            conn.close()
