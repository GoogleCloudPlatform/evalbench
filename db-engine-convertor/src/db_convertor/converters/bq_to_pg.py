"""BigQuery to PostgreSQL database converter."""

from typing import Dict, Optional
from .base import DatabaseConverter
from ..exporters.bigquery_exporter import BigQueryExporter
from ..importers.pg_importer import PostgreSQLImporter


class BQToPGConverter(DatabaseConverter):
    """Converter for migrating BigQuery datasets to PostgreSQL."""
    
    def get_exporter(self) -> BigQueryExporter:
        """Get BigQuery exporter."""
        return BigQueryExporter(self.config.source_connection)
    
    def get_importer(self) -> PostgreSQLImporter:
        """Get PostgreSQL importer."""
        return PostgreSQLImporter(self.config.target_connection)
    
    def get_schema_conversion_prompt(self, source_schema: str, 
                                     csv_summaries: Dict, 
                                     prev_schema: Optional[str] = None,
                                     prev_convertor: Optional[str] = None,
                                     error: Optional[str] = None) -> str:
        """Get prompt for BigQuery to PostgreSQL schema conversion."""
        target_db_type = "postgresql"
        prompt = f"""
You are an expert database engineer converting a BigQuery dataset schema to PostgreSQL.

SOURCE SCHEMA (BigQuery Pseudo-SQL):
----------------------------------
{source_schema}

DATA SAMPLES (CSV format):
-------------------------
"""
        # Add CSV summaries
        for table, summary in csv_summaries.items():
            prompt += f"\nTable: {table}\n"
            prompt += f"Columns: {', '.join(summary['columns'])}\n"
            prompt += f"First 5 rows:\n"
            for row in summary['first_lines']:
                prompt += f"  {row}\n"

        prompt += f"""
TARGET DATABASE: {target_db_type}
----------------------------------

TASK:
1. Generate PostgreSQL `CREATE TABLE` statements.
2. Generate a Python data conversion script.

### 1. SQL Schema Rules:
- Map BigQuery types to PostgreSQL:
  - `INT64` -> `BIGINT`
  - `FLOAT64` -> `DOUBLE PRECISION`
  - `STRING` -> `TEXT` or `VARCHAR(N)`
  - `BOOL` -> `BOOLEAN`
  - `TIMESTAMP` -> `TIMESTAMP WITH TIME ZONE`
  - `DATE` -> `DATE`
  - `BYTES` -> `BYTEA`
- Use double quotes `"` for all table and column names to handle case-sensitivity and reserved words.
- Define Primary Keys if you can infer them from the data or column names (e.g., columns ending in 'id' or 'code').
- BigQuery doesn't have explicit Primary Keys or Foreign Keys, so use your best judgment.

### 2. Python Data Conversion Script Rules:
Generate a Python script that:
- Reads CSV files from `source_dir`.
- Cleans data for PostgreSQL compatibility:
  - Handle BigQuery's exported NULLs (usually empty strings in CSV).
  - Ensure boolean values are 'true'/'false' or 1/0 as per PostgreSQL preference.
  - Correctly format timestamps if necessary.
- Writes cleaned CSVs to `dest_dir` with the same filename.
- Includes a `convert_data(source_dir, dest_dir)` function.
- Includes an `if __name__ == "__main__":` block that takes `source_dir` and `dest_dir` as arguments.

OUTPUT FORMAT:
Return ONLY a JSON object with:
{{
  "postgresql_schema": "the postgresql ddl...",
  "data_convertor": "the python script text...",
  "notes": "any implementation notes..."
}}
"""
        # Add feedback from previous iteration if any
        if prev_schema and error:
            prompt += f"""
PREVIOUS ATTEMPT FAILED:
------------------------
Previous Schema:
{prev_schema}

Error Message from Pipeline:
{error}

INSTRUCTION: Fix the schema/script based on the error above.
"""
        return prompt
