"""BigQuery database exporter."""

from pathlib import Path
from typing import List, Tuple, Dict
import csv
from google.cloud import bigquery
from .base import DatabaseExporter


class BigQueryExporter(DatabaseExporter):
    """Exporter for BigQuery databases (datasets)."""
    
    def __init__(self, connection: Dict[str, str]):
        """Initialize BigQuery exporter.
        
        Args:
            connection: Dict with project_id, dataset_id
        """
        # BigQuery connection string in base is not used directly
        super().__init__(f"bigquery://{connection['project_id']}/{connection['dataset_id']}")
        self.project_id = connection['project_id']
        self.dataset_id = connection['dataset_id']
        self.client = bigquery.Client(project=self.project_id)
    
    def export_schema(self, output_path: Path) -> str:
        """Export BigQuery schema to a pseudo-SQL file.
        
        Args:
            output_path: Path to write schema file
            
        Returns:
            Path to the exported schema file
        """
        tables = self.get_tables()
        schema_statements = []
        
        for table_name in tables:
            table_ref = self.client.dataset(self.dataset_id).table(table_name)
            table = self.client.get_table(table_ref)
            
            # Reconstruct CREATE TABLE statement for AI understanding
            create_stmt = f'CREATE TABLE `{table_name}` (\n'
            column_defs = []
            
            for field in table.schema:
                col_def = f'  `{field.name}` {field.field_type}'
                if field.mode == 'REQUIRED':
                    col_def += ' NOT NULL'
                if field.description:
                    col_def += f' -- {field.description}'
                column_defs.append(col_def)
            
            create_stmt += ',\n'.join(column_defs)
            create_stmt += '\n);'
            schema_statements.append(create_stmt)
            
        # Write to file
        output_path = Path(output_path)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("-- BigQuery Dataset Schema Export (Pseudo-SQL)\n")
            f.write(f"-- Project: {self.project_id}\n")
            f.write(f"-- Dataset: {self.dataset_id}\n\n")
            for statement in schema_statements:
                f.write(statement + '\n\n')
        
        return str(output_path)
    
    def get_tables(self) -> List[str]:
        """Get list of all tables in the dataset.
        
        Returns:
            List of table names
        """
        dataset_ref = self.client.dataset(self.dataset_id)
        tables = list(self.client.list_tables(dataset_ref))
        return [table.table_id for table in tables]
    
    def export_table_data(self, table_name: str, output_path: Path) -> Tuple[int, str]:
        """Export table data to CSV.
        
        Args:
            table_name: Name of the table to export
            output_path: Path to write CSV file
            
        Returns:
            Tuple of (row_count, csv_path)
        """
        table_ref = self.client.dataset(self.dataset_id).table(table_name)
        
        # Using list_rows().to_dataframe() for convenience
        # If the table is extremely large, might need to use extract_table to GCS or pagination
        try:
            df = self.client.list_rows(table_ref).to_dataframe()
            row_count = len(df)
            df.to_csv(output_path, index=False, quoting=csv.QUOTE_MINIMAL)
            return row_count, str(output_path)
        except Exception as e:
            # Fallback for large tables or if pandas fails
            print(f"Warning: BigQuery to_dataframe failed for {table_name}: {e}. Trying manual export.")
            rows = self.client.list_rows(table_ref)
            row_count = 0
            with open(output_path, 'w', newline='', encoding='utf-8') as f:
                writer = csv.writer(f)
                # Write header
                writer.writerow([field.name for field in rows.schema])
                for row in rows:
                    writer.writerow(list(row.values()))
                    row_count += 1
            return row_count, str(output_path)

    def __enter__(self):
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass
