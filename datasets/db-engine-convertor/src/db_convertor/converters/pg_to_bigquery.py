"""PostgreSQL to BigQuery converter."""

from typing import Dict, Optional
from ..converters.base import DatabaseConverter
from ..importers.bigquery_importer import BigQueryImporter
from ..exporters.pg_exporter import PostgreSQLExporter


class PGToBigQueryConverter(DatabaseConverter):
    """Converter for PostgreSQL to Google Cloud BigQuery."""
    
    def get_exporter(self):
        """Get PostgreSQL exporter."""
        return PostgreSQLExporter(self.config.source_connection)
    
    def get_importer(self):
        """Get BigQuery importer."""
        return BigQueryImporter(self.config.target_connection)
        
    def get_schema_conversion_prompt(self, source_schema: str, 
                                     csv_summaries: Dict,
                                     prev_schema: Optional[str] = None,
                                     prev_convertor: Optional[str] = None,
                                     error: Optional[str] = None) -> str:
        """Get prompt for BigQuery schema conversion from PostgreSQL."""
        
        prompt = f"""
You are an expert database engineer migrating a database from PostgreSQL to Google Cloud BigQuery.
Your goal is to generate a Google Cloud BigQuery schema and a Python data conversion script.

SOURCE DATABASE (PostgreSQL):
-------------------------
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
        
        prompt += """
TASK:
-----
1. Generate a Google Cloud BigQuery schema (`bigquery_schema.sql`) that:
   - Uses BigQuery Standard SQL data types: INT64, FLOAT64, BOOL, STRING, BYTES, DATE, TIMESTAMP, JSON.
   - Converts PostgreSQL types appropriately:
     - SERIAL/BIGSERIAL -> INT64
     - INTEGER/BIGINT -> INT64
     - REAL/DOUBLE PRECISION -> FLOAT64
     - VARCHAR(n)/TEXT -> STRING
     - BYTEA -> BYTES
     - BOOLEAN -> BOOL
     - DATE -> DATE
     - TIMESTAMP/TIMESTAMPTZ -> TIMESTAMP
     - JSON/JSONB -> JSON
   - **CRITICAL**: Do NOT generate `FOREIGN KEY` constraints. BigQuery treats them as informational and they are not enforced during load.
   - **CRITICAL**: Every table should have appropriate column definitions. BigQuery does not have a formal `PRIMARY KEY` enforcement like PostgreSQL, but you can define columns as required (NOT NULL) if they are primary keys in the source.
   - Use backticks ` for identifiers if they contain spaces or are reserved words.
   
2. Generate a Python data conversion script (`data_convertor.py`) that:
   - Reads CSV files from `source_dir`
   - Transforms data to match BigQuery strict types:
     - Convert PostgreSQL booleans ('t'/'f', 'true'/'false') to Python True/False (BigQuery CSV loader handles these well if cleaned).
     - Handle PostgreSQL timestamps (convert to ISO format if needed).
     - Handle JSONB/JSON (ensure they are valid JSON strings).
   - Handles NULLs correctly (PostgreSQL may export \\\\N for NULL, which should be converted to empty/null for BigQuery CSV loading).
   - Writes cleaned CSVs to `dest_dir` with the EXACT SAME filename as the source (e.g., `schools.csv`). Do NOT add suffixes like `_cleaned`.
   - The script must have a `convert_data(source_dir, dest_dir)` function.
   - **CRITICAL**: The script MUST include an `if __name__ == "__main__":` block that takes source and dest dirs as arguments.

OUTPUT FORMAT:
--------------
Return ONLY a JSON object with these keys:
{
  "bigquery_schema": "current schema SQL...",
  "data_convertor": "complete python script..."
}
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
