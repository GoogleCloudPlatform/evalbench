"""BigQuery database importer."""

from pathlib import Path
from typing import Dict, List
from google.cloud import bigquery

from .base import DatabaseImporter


class BigQueryImporter(DatabaseImporter):
    """Importer for Google Cloud BigQuery."""
    
    def __init__(self, connection_config: Dict[str, str]):
        """Initialize importer.
        
        Args:
            connection_config: Dict containing:
                - project_id
                - dataset_id
        """
        super().__init__(connection_config)
        self.project_id = connection_config['project_id']
        self.dataset_id = connection_config['dataset_id']
        
        self.client = bigquery.Client(project=self.project_id)
        self.dataset_ref = bigquery.DatasetReference(self.project_id, self.dataset_id)
    
    def wipe_database(self):
        """Wipe all tables from the dataset."""
        print(f"Wiping dataset {self.dataset_id} in BigQuery...")
        
        try:
            tables = list(self.client.list_tables(self.dataset_ref))
            if not tables:
                print("No tables found to wipe.")
                return
                
            for table in tables:
                table_id = f"{self.project_id}.{self.dataset_id}.{table.table_id}"
                print(f"  Deleting table {table.table_id}...")
                self.client.delete_table(table_id, not_found_ok=True)
            print("Wipe completed.")
        except Exception as e:
            print(f"Error during wipe: {e}")
            raise e

    def create_schema(self, schema_file: Path):
        """Create schema in the database.
        
        Args:
            schema_file: Path to schema SQL file
        """
        with open(schema_file, 'r', encoding='utf-8') as f:
            schema_sql = f.read()
            
        if not schema_sql.strip():
            print("No statements found in schema file")
            return

        print(f"Applying DDL statements to BigQuery dataset {self.dataset_id} in project {self.project_id}...")
        
        job_config = bigquery.QueryJobConfig(default_dataset=self.dataset_ref)
        
        try:
            query_job = self.client.query(schema_sql, job_config=job_config)
            query_job.result()  # Wait for job to complete
            print("Schema created successfully.")
        except Exception as e:
            print(f"Error creating schema: {e}")
            raise e

    def load_csv_data(self, csv_dir: Path, tables: List[str]):
        """Load CSV data into database tables.
        
        Args:
            csv_dir: Directory containing CSV files
            tables: List of tables in dependency order
        """
        if not tables:
            print("No tables provided for data load.")
            return

        for table_name in tables:
            csv_path = csv_dir / f"{table_name}.csv"
            if not csv_path.exists():
                # Try with _cleaned suffix as some generated scripts add it
                csv_path = csv_dir / f"{table_name}_cleaned.csv"
                
            if not csv_path.exists():
                raise FileNotFoundError(f"No data file for table {table_name} (checked {table_name}.csv and {table_name}_cleaned.csv)")
            
            print(f"Loading {table_name} into BigQuery from {csv_path.name}...")
            
            table_id = f"{self.project_id}.{self.dataset_id}.{table_name}"
            
            job_config = bigquery.LoadJobConfig(
                source_format=bigquery.SourceFormat.CSV,
                skip_leading_rows=1,
                autodetect=False,  # We rely on existing schema
                write_disposition=bigquery.WriteDisposition.WRITE_APPEND,
            )
            
            with open(csv_path, "rb") as source_file:
                job = self.client.load_table_from_file(source_file, table_id, job_config=job_config)
                
            try:
                job.result()  # Wait for job to complete
                print(f"  Successfully loaded {job.output_rows} rows into {table_name}.")
            except Exception as e:
                print(f"Error loading {table_name}: {e}")
                if hasattr(job, 'errors') and job.errors:
                    for err in job.errors:
                        print(f"    - {err}")
                raise

    def get_table_dependencies(self) -> List[str]:
        """Get tables in dependency order."""
        print(f"Listing tables in BigQuery dataset: {self.dataset_id}...")
        tables = list(self.client.list_tables(self.dataset_ref))
        table_ids = sorted([t.table_id for t in tables])
        print(f"Found {len(table_ids)} tables: {table_ids}")
        return table_ids

    def verify_row_counts(self, expected_counts: Dict[str, int]) -> bool:
        """Verify row counts."""
        success = True
        print("\nVerifying row counts in BigQuery:")
        print(f"{'Table':<30} {'Expected':<10} {'Actual':<10} {'Status'}")
        print("-" * 60)
        
        for table, expected in expected_counts.items():
            table_id = f"{self.project_id}.{self.dataset_id}.{table}"
            try:
                query = f"SELECT COUNT(*) as count FROM `{table_id}`"
                results = self.client.query(query).to_dataframe()
                actual = int(results['count'][0])
                
                status = "✓" if expected == actual else "✗"
                if expected != actual:
                    success = False
                
                print(f"{table:<30} {expected:<10} {actual:<10} {status}")
            except Exception as e:
                print(f"{table:<30} {expected:<10} {'ERROR':<10} ✗ ({e})")
                success = False
                
        return success
