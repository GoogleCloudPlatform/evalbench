"""PostgreSQL to Spanner converter."""

from typing import Dict, Optional
from ..converters.base import DatabaseConverter
from ..importers.spanner_importer import SpannerImporter
from ..exporters.pg_exporter import PostgreSQLExporter


class PGToSpannerConverter(DatabaseConverter):
    """Converter for PostgreSQL to Google Cloud Spanner."""
    
    def get_exporter(self):
        """Get PostgreSQL exporter."""
        return PostgreSQLExporter(self.config.source_connection)
    
    def get_importer(self):
        """Get Spanner importer."""
        return SpannerImporter(self.config.target_connection)
        
    def get_schema_conversion_prompt(self, source_schema: str, 
                                     csv_summaries: Dict,
                                     prev_schema: Optional[str] = None,
                                     prev_convertor: Optional[str] = None,
                                     error: Optional[str] = None) -> str:
        """Get prompt for Spanner schema conversion from PostgreSQL."""
        
        prompt = f"""
You are an expert database engineer migrating a database from PostgreSQL to Google Cloud Spanner.
Your goal is to generate a Google Cloud Spanner schema and a Python data conversion script.

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
1. Generate a Google Cloud Spanner schema (`spanner_schema.sql`) that:
   - Uses strict Spanner data types: INT64, FLOAT64, BOOL, STRING, BYTES, DATE, TIMESTAMP.
   - Converts PostgreSQL types appropriately:
     - SERIAL/BIGSERIAL -> INT64
     - INTEGER/BIGINT -> INT64
     - REAL/DOUBLE PRECISION -> FLOAT64
     - VARCHAR(n)/TEXT -> STRING(MAX) or STRING(n)
     - BYTEA -> BYTES(MAX)
     - BOOLEAN -> BOOL
     - DATE -> DATE
     - TIMESTAMP/TIMESTAMPTZ -> TIMESTAMP
     - JSON/JSONB -> STRING(MAX) (store as string for simplicity)
   - **CRITICAL**: Do NOT generate `FOREIGN KEY` constraints. The source data may have integrity issues, so we must allow loose relationships.
   - **CRITICAL**: Do NOT use `INTERLEAVE IN PARENT`. Treat all tables as top-level tables.
   - **CRITICAL**: Every table MUST have a PRIMARY KEY.
   - Handling Auto-Increment: Spanner DOES NOT support `SERIAL`. 
     - Data is loaded from CSVs which already contain the IDs. Just define the column as INT64.
   - Use backticks ` for identifiers.
   - **IMPORTANT**: Sanitize column names to be valid Spanner identifiers.
     - Spanner identifiers must start with a letter (a-z, A-Z) or underscore (_). They CANNOT start with a number.
     - They must contain only alphanumeric characters and underscores.
     - You MUST rename columns in the schema AND the `data_convertor.py` script so CSV headers match.
   
2. Generate a Python data conversion script (`data_convertor.py`) that:
   - Reads CSV files from `source_dir`
   - Transforms data to match Spanner strict types:
     - Convert PostgreSQL booleans ('t'/'f', 'true'/'false') to Python True/False.
     - Handle PostgreSQL timestamps (convert to ISO format if needed).
     - Handle JSONB/JSON (ensure they are valid strings).
   - Handles NULLs correctly (PostgreSQL may export \\N for NULL).
   - Writes cleaned CSVs to `dest_dir`.
   - The script must have a `convert_data(source_dir, dest_dir)` function.
   - **CRITICAL**: The script MUST include an `if __name__ == "__main__":` block that takes source and dest dirs as arguments.

OUTPUT FORMAT:
--------------
Return ONLY a JSON object with these keys:
{
  "spanner_schema": "current schema SQL...",
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
