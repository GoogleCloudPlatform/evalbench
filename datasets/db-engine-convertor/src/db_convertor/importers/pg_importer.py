"""PostgreSQL database importer."""

import subprocess
import os
from pathlib import Path
from typing import Dict, List
from .base import DatabaseImporter


class PipelineError(Exception):
    """Custom exception for pipeline errors."""
    pass


class PostgreSQLImporter(DatabaseImporter):
    """Importer for PostgreSQL databases."""
    
    def __init__(self, connection_config: Dict[str, str]):
        """Initialize PostgreSQL importer.
        
        Args:
            connection_config: Dict with keys: host, port, user, password, database
        """
        super().__init__(connection_config)
        self.host = connection_config['host']
        self.port = connection_config.get('port', '5432')
        self.user = connection_config['user']
        self.password = connection_config['password']
        self.database = connection_config['database']
        
        # Set password environment variable
        os.environ['PGPASSWORD'] = self.password
    
    def _run_command(self, cmd: str, capture_output=True, check=True) -> subprocess.CompletedProcess:
        """Run a shell command and return the result.
        
        Args:
            cmd: Command to run
            capture_output: Whether to capture output
            check: Whether to check return code
            
        Returns:
            CompletedProcess instance
            
        Raises:
            PipelineError: If command fails
        """
        print(f"Running: {cmd}")
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=capture_output,
            text=True
        )
        
        if check and result.returncode != 0:
            error_msg = f"Command failed with exit code {result.returncode}\n"
            error_msg += f"Command: {cmd}\n"
            if result.stdout:
                error_msg += f"STDOUT:\n{result.stdout}\n"
            if result.stderr:
                error_msg += f"STDERR:\n{result.stderr}\n"
            raise PipelineError(error_msg)
        
        return result
    
    def wipe_database(self):
        """Wipe all tables from the PostgreSQL database."""
        print("\n" + "=" * 80)
        print("STEP: Wiping destination database")
        print("=" * 80)
        
        # Get all tables
        cmd = f"psql -h {self.host} -p {self.port} -U {self.user} -d {self.database} -t -c \"SELECT tablename FROM pg_tables WHERE schemaname='public'\""
        result = self._run_command(cmd, capture_output=True, check=True)
        
        tables = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
        
        if tables:
            print(f"Found {len(tables)} tables to drop: {', '.join(tables)}")
            
            # Drop tables with CASCADE
            drop_cmd = f"psql -h {self.host} -p {self.port} -U {self.user} -d {self.database} -c \"DROP SCHEMA public CASCADE; CREATE SCHEMA public;\""
            self._run_command(drop_cmd, capture_output=True, check=True)
            print("✓ Database wiped successfully")
        else:
            print("✓ Database is already empty")
    
    def create_schema(self, schema_file: Path):
        """Create schema in PostgreSQL database.
        
        Args:
            schema_file: Path to schema SQL file
        """
        print("\n" + "=" * 80)
        print("STEP: Creating schema")
        print("=" * 80)
        
        cmd = f"psql -h {self.host} -p {self.port} -U {self.user} -d {self.database} -f {schema_file}"
        result = self._run_command(cmd, capture_output=True, check=True)
        
        print("✓ Schema created successfully")
        if result.stdout:
            print(result.stdout)
    
    def get_table_dependencies(self) -> List[str]:
        """Get tables in dependency order based on foreign keys.
        
        Returns:
            List of table names in load order
        """
        # Get all tables
        cmd = f"""psql -h {self.host} -p {self.port} -U {self.user} -d {self.database} -t -c "
            SELECT DISTINCT tablename 
            FROM pg_tables 
            WHERE schemaname='public' 
            ORDER BY tablename
        \""""
        result = self._run_command(cmd, capture_output=True, check=True)
        all_tables = [line.strip() for line in result.stdout.strip().split('\n') if line.strip()]
        
        # Get foreign key relationships
        cmd = f"""psql -h {self.host} -p {self.port} -U {self.user} -d {self.database} -t -c "
            SELECT 
                tc.table_name,
                ccu.table_name AS foreign_table_name
            FROM 
                information_schema.table_constraints AS tc 
                JOIN information_schema.key_column_usage AS kcu
                  ON tc.constraint_name = kcu.constraint_name
                  AND tc.table_schema = kcu.table_schema
                JOIN information_schema.constraint_column_usage AS ccu
                  ON ccu.constraint_name = tc.constraint_name
                  AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema='public'
        \""""
        result = self._run_command(cmd, capture_output=True, check=True)
        
        dependencies = {}
        for line in result.stdout.strip().split('\n'):
            if '|' in line:
                parts = [p.strip() for p in line.split('|')]
                if len(parts) == 2:
                    table, ref_table = parts
                    if table not in dependencies:
                        dependencies[table] = []
                    dependencies[table].append(ref_table)
        
        # Topological sort
        ordered = []
        visited = set()
        
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
    
    def load_csv_data(self, csv_dir: Path, tables: List[str] = None):
        """Load CSV data into PostgreSQL tables.
        
        Args:
            csv_dir: Directory containing CSV files
            tables: List of tables in dependency order (if None, will determine automatically)
        """
        print("\n" + "=" * 80)
        print("STEP: Uploading CSV files to PostgreSQL")
        print("=" * 80)
        
        csv_files = {f.stem: f for f in Path(csv_dir).glob('*.csv')}
        
        if not csv_files:
            raise PipelineError(f"No CSV files found in {csv_dir}. CSV convertor failed to create output files.")
        
        # Get table dependency order if not provided
        if tables is None:
            print("  Determining table load order based on foreign keys...")
            tables = self.get_table_dependencies()
            print(f"  Load order: {' -> '.join(tables)}")
        
        for table_name in tables:
            if table_name not in csv_files:
                print(f"  Warning: No CSV file for table {table_name}, skipping")
                continue
            
            csv_file = csv_files[table_name]
            print(f"  Uploading {csv_file.name} to table {table_name}...")
            
            # Use COPY command to load CSV
            copy_cmd = f"\\COPY {table_name} FROM '{csv_file.absolute()}' WITH (FORMAT csv, HEADER true, ENCODING 'UTF-8')"
            cmd = f"psql -h {self.host} -p {self.port} -U {self.user} -d {self.database} -c \"{copy_cmd}\""
            
            try:
                self._run_command(cmd, capture_output=True, check=True)
                
                # Count rows
                count_cmd = f"psql -h {self.host} -p {self.port} -U {self.user} -d {self.database} -t -c \"SELECT COUNT(*) FROM {table_name}\""
                count_result = self._run_command(count_cmd, capture_output=True, check=True)
                row_count = count_result.stdout.strip()
                
                print(f"    ✓ Uploaded {row_count} rows")
            except PipelineError:
                print(f"    ✗ Failed to upload {csv_file.name}")
                raise
    
    def verify_row_counts(self, expected_counts: Dict[str, int]) -> bool:
        """Verify row counts match expected values.
        
        Args:
            expected_counts: Dict mapping table names to expected row counts
            
        Returns:
            True if all counts match, False otherwise
        """
        all_match = True
        for table_name, expected in expected_counts.items():
            cmd = f"psql -h {self.host} -p {self.port} -U {self.user} -d {self.database} -t -c \"SELECT COUNT(*) FROM {table_name}\""
            result = self._run_command(cmd, capture_output=True, check=True)
            actual = int(result.stdout.strip())
            
            if actual != expected:
                print(f"✗ Row count mismatch for {table_name}: expected {expected}, got {actual}")
                all_match = False
            else:
                print(f"✓ Row count match for {table_name}: {actual}")
        
        return all_match

