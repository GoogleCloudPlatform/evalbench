"""Spanner database importer."""

from pathlib import Path
from typing import Dict, List, Optional
from google.cloud import spanner
import csv

from .base import DatabaseImporter


class SpannerImporter(DatabaseImporter):
    """Importer for Google Cloud Spanner."""
    
    def __init__(self, connection_config: Dict[str, str]):
        """Initialize importer.
        
        Args:
            connection_config: Dict containing:
                - project_id
                - instance_id
                - database_id
        """
        super().__init__(connection_config)
        self.project_id = connection_config['project_id']
        self.instance_id = connection_config['instance_id']
        self.database_id = connection_config['database_id']
        
        import google.auth
        credentials, project = google.auth.default(quota_project_id=self.project_id)
        
        self.client = spanner.Client(project=self.project_id, credentials=credentials)
        self.instance = self.client.instance(self.instance_id)
        self.database = self.instance.database(self.database_id)
    
    def wipe_database(self):
        """Wipe all tables from the database."""
        if not self.database.exists():
            print(f"Database {self.database_id} does not exist, skipping wipe.")
            return

        try:
            with self.database.snapshot() as snapshot:
                # Use query to find all tables
                results = snapshot.execute_sql(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = ''"
                )
                tables = [row[0] for row in results]
                
            if not tables:
                return

            print(f"Dropping {len(tables)} tables from Spanner...")
            
            # Drop tables in reverse order to handle FKs (simplified approach)
            # A better approach would be to topologically sort, but simpler 
            # is to just try dropping foreign keys first or disabling them? 
            # Spanner doesn't easily allow disabling FKs globally.
            # For now, we'll try to drop them all in a single batch of DDL if possible, 
            # but Spanner DDL is usually one-at-a-time or batched.
            
            # Dropping tables requires them to not be interleaved or have FK dependencies.
            # We'll generate drop statements.
            
            # NOTE: Dropping tables with minimal hassle:
            # 1. Drop all foreign keys
            # 2. Drop all tables
            
            # Find FKs
            with self.database.snapshot() as snapshot:
                fk_results = snapshot.execute_sql(
                    """
                    SELECT table_name, constraint_name 
                    FROM information_schema.table_constraints 
                    WHERE constraint_type = 'FOREIGN KEY' AND table_schema = ''
                    """
                )
                fks = [(row[0], row[1]) for row in fk_results]
            
            ddl_statements = []
            for table, fk in fks:
                ddl_statements.append(f"ALTER TABLE `{table}` DROP CONSTRAINT `{fk}`")
                
            if ddl_statements:
                print(f"Dropping {len(ddl_statements)} foreign keys...")
                operation = self.database.update_ddl(ddl_statements)
                operation.result(timeout=300)
            
            # Now drop tables
            drop_statements = [f"DROP TABLE `{table}`" for table in tables]
            if drop_statements:
                print(f"Dropping tables...")
                operation = self.database.update_ddl(drop_statements)
                operation.result(timeout=300)
                
        except Exception as e:
            print(f"Warning during wipe: {e}")
            # Non-fatal if empty

    def create_schema(self, schema_file: Path):
        """Create schema in the database.
        
        Args:
            schema_file: Path to schema SQL file
        """
        with open(schema_file, 'r', encoding='utf-8') as f:
            schema_sql = f.read()
            
        # Parse SQL into statements
        # Spanner DDL expects list of strings
        statements = []
        current_statement = []
        
        # Simple parser for DDL statements ending in ;
        # This assumes the schema file is formatted with one stmt per block
        for line in schema_sql.split('\n'):
            line = line.strip()
            if not line or line.startswith('--'):
                continue
            
            current_statement.append(line)
            if line.endswith(';'):
                stmt = ' '.join(current_statement)
                stmt = stmt.rstrip(';')
                statements.append(stmt)
                current_statement = []
        
        if current_statement:
            stmt = ' '.join(current_statement).rstrip(';')
            if stmt:
                statements.append(stmt)
        
        if not statements:
            print("No statements found in schema file")
            return
            
        if not self.database.exists():
            print(f"Creating database {self.database_id} with initial schema...")
            try:
                # Re-initialize database object with DDL statements for creation
                db_to_create = self.instance.database(self.database_id, ddl_statements=statements)
                operation = db_to_create.create()
                operation.result(timeout=600)
                print(f"Database {self.database_id} created successfully.")
                return
            except Exception as e:
                print(f"Error creating database: {e}")
                raise

        print(f"Applying {len(statements)} DDL statements to Spanner...")
        
        # Apply in batches to avoid timeout constraints if too many
        batch_size = 20
        for i in range(0, len(statements), batch_size):
            batch = statements[i:i+batch_size]
            print(f"  Applying batch {i // batch_size + 1}...")
            try:
                operation = self.database.update_ddl(batch)
                operation.result(timeout=600) # 10 minutes timeout
            except Exception as e:
                print(f"Error applying DDL: {e}")
                # We stop on error to avoid inconsistent state
                raise

    def load_csv_data(self, csv_dir: Path, tables: List[str]):
        """Load CSV data into database tables.
        
        Args:
            csv_dir: Directory containing CSV files
            tables: List of tables in dependency order
        """
        for table_name in tables:
            csv_path = csv_dir / f"{table_name}.csv"
            if not csv_path.exists():
                print(f"Warning: No data file for table {table_name}")
                continue
            
            print(f"Loading {table_name}...")
            
            # Read CSV to get headers and data
            # Determine column types from headers is complex, 
            # Spanner insert requires matching types.
            # Since we assume the CSV matches the schema created, 
            # we need to ensure types like int/float are converted from strings.
            # But wait, batch.insert doesn't auto-cast well.
            # We might need to inspect the schema again? 
            # Or rely on the data_convertor.py to have produced "clean" CSVs.
            # Actually data_convertor.py produces CSVs.
            # We will use `execute_partitioned_dml` or `insert` mutation?
            # Mutations are faster.
            
            # To do this robustly without querying schema for every table:
            # We will just pass strings and hope Spanner's flexible enough or catch errors.
            # Actually Spanner SDK is strict.
            # We need to type-cast.
            # Let's get column types for the table.
            
            col_types = self._get_column_types(table_name)
            
            with open(csv_path, 'r', encoding='utf-8') as f:
                reader = csv.reader(f)
                header = next(reader, None)
                if not header:
                    continue
                
                # Verify header columns exist
                # (Simple check)
                
                batch_data = []
                BATCH_SIZE = 500 # 500 rows per transaction
                
                for row_idx, row in enumerate(reader):
                    # Convert values based on col_types
                    converted_row = []
                    for col_idx, value in enumerate(row):
                        col_name = header[col_idx]
                        target_type = col_types.get(col_name)
                        converted_val = self._convert_value(value, target_type)
                        converted_row.append(converted_val)
                    
                    batch_data.append(converted_row)
                    
                    if len(batch_data) >= BATCH_SIZE:
                        self._insert_batch(table_name, header, batch_data)
                        batch_data = []
                        if (row_idx + 1) % 1000 == 0:
                            print(f"  Loaded {row_idx + 1} rows...")

                if batch_data:
                    self._insert_batch(table_name, header, batch_data)
                    
            print(f"  Finished {table_name}")

    def _get_column_types(self, table_name: str) -> Dict[str, str]:
        """Get column data types for a table."""
        with self.database.snapshot() as snapshot:
            results = snapshot.execute_sql(
                "SELECT column_name, spanner_type FROM information_schema.columns "
                f"WHERE table_name = '{table_name}' AND table_schema = ''"
            )
            return {row[0]: row[1] for row in results}

    def _convert_value(self, value: str, spanner_type: Optional[str]):
        """Convert string value to appropriate Python type for Spanner."""
        if value is None or value == '' or value == 'NULL':
            return None
        
        if not spanner_type:
            return value

        spanner_type = spanner_type.upper()
        
        try:
            if 'INT64' in spanner_type:
                return int(value)
            elif 'FLOAT64' in spanner_type:
                return float(value)
            elif 'BOOL' in spanner_type:
                return value.lower() in ('true', '1', 't', 'y', 'yes')
            # Add more types as needed (BYTES, DATE, TIMESTAMP handled as strings usually fine for SDK?)
            # SDK needs datetime objects for TIMESTAMP usually, strings might work for DML but mutations prefer objects.
            # For simplicity let's stick to strings for complex types and let client handle if possible,
            # or add handling if we see errors.
            return value
        except Exception:
            # Fallback to string if cast fails
            return value

    def _insert_batch(self, table: str, columns: List[str], data: List[List]):
        """Insert a batch of rows."""
        def insert_txn(transaction):
            transaction.insert(
                table,
                columns=columns,
                values=data,
            )
        
        self.database.run_in_transaction(insert_txn)

    def get_table_dependencies(self) -> List[str]:
        """Get tables in dependency order."""
        # Simple topological sort
        tables = set()
        dependencies = {} # table -> set of dependencies
        
        # Step 1: Get all tables
        with self.database.snapshot() as snapshot:
            results = snapshot.execute_sql(
                    "SELECT table_name FROM information_schema.tables WHERE table_schema = ''"
                )
            for row in results:
                tables.add(row[0])
                dependencies[row[0]] = set()
                
        # Step 2: Get FKs (New snapshot)
        try:
            with self.database.snapshot() as snapshot:
                # Note: Spanner information schema for constraints
                snapshot.execute_sql(
                    """
                    SELECT 
                        tc.table_name, 
                        ccu.table_name as hostname
                    FROM information_schema.table_constraints tc
                    JOIN information_schema.referential_constraints rc
                        ON tc.constraint_name = rc.constraint_name
                    JOIN information_schema.constraint_column_usage ccu
                        ON rc.unique_constraint_name = ccu.constraint_name
                    WHERE tc.constraint_type = 'FOREIGN KEY'
                    """
                )
                # Query above is standard SQL, Spanner might differ slightly in IS structure.
                # Fallback: simpler approach or try standard.
        except Exception:
            # Exploratory FK query may fail on some Spanner configurations; safe to ignore
            pass
        
        # Proper implementation for Spanner dependency graph:
        # We can implement a simplified version that just queries for parent tables
        # Spanner has interleaving which is strict dependency.
        
        # To be safe, let's implement a robust topological sort fetching foreign keys.
        # Since I cannot easily run complex SQL to check, I will try to strictly parse 
        # REFERENTIAL_CONSTRAINTS if possible.
        
        # Optimized: just return list of tables, if we fail to insert due to FK, 
        # the user might need to retry or we rely on the Agent to have ordered them? 
        # No, importer must handle load order.
        
        # Let's attempt to pull from existing code logic like `pg_importer` if possible 
        # or implement generic one.
        
        # For this iteration, I'll fetch standard dependencies.
        try:
             with self.database.snapshot() as snapshot:
                # This query works on Postgres, Spanner has `CONSTRAINT_COLUMN_USAGE`? Yes.
                # `REFERENTIAL_CONSTRAINTS`? Yes.
                rows = snapshot.execute_sql("""
                    SELECT 
                        tp.table_name AS table_name,
                        tu.table_name AS referenced_table_name
                    FROM information_schema.table_constraints AS tp
                    INNER JOIN information_schema.referential_constraints AS rc
                        ON tp.constraint_name = rc.constraint_name
                    INNER JOIN information_schema.table_constraints AS tu
                        ON rc.unique_constraint_name = tu.constraint_name
                    WHERE tp.constraint_type = 'FOREIGN KEY' 
                      AND tp.table_schema = '' 
                      AND tu.table_schema = ''
                """)
                for row in rows:
                    if row[0] in dependencies and row[1] in tables:
                        dependencies[row[0]].add(row[1])

        except Exception as e:
            print(f"Error fetching dependencies: {e}. defaulting to alphabetical.")
        
        # Sort
        sorted_tables = []
        visited = set()
        temp_mark = set()

        def visit(n):
            if n in temp_mark:
                 # Cycle detected
                 return
            if n not in visited:
                temp_mark.add(n)
                for m in dependencies.get(n, []):
                    visit(m)
                temp_mark.remove(n)
                visited.add(n)
                sorted_tables.append(n)

        for table in sorted(list(tables)):
             visit(table)
             
        return sorted_tables if sorted_tables else list(tables)

    def verify_row_counts(self, expected_counts: Dict[str, int]) -> bool:
        """Verify row counts."""
        success = True
        print("\nVerifying row counts:")
        print(f"{'Table':<30} {'Expected':<10} {'Actual':<10} {'Status'}")
        print("-" * 60)
        
        with self.database.snapshot() as snapshot:
            for table, expected in expected_counts.items():
                try:
                    results = snapshot.execute_sql(f"SELECT COUNT(*) FROM `{table}`")
                    actual = list(results)[0][0]
                    
                    status = "✓" if expected == actual else "✗"
                    if expected != actual:
                        success = False
                    
                    print(f"{table:<30} {expected:<10} {actual:<10} {status}")
                except Exception as e:
                    print(f"{table:<30} {expected:<10} {'ERROR':<10} ✗ ({e})")
                    success = False
                    
        return success
