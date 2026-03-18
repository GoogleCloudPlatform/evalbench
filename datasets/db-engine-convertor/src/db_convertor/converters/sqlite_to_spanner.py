"""SQLite to Spanner converter."""

from typing import Dict, Optional
from ..converters.base import DatabaseConverter
from ..importers.spanner_importer import SpannerImporter
from ..exporters.sqlite_exporter import SQLiteExporter


class SQLiteToSpannerConverter(DatabaseConverter):
    """Converter for SQLite to Google Cloud Spanner."""
    
    def get_exporter(self):
        """Get SQLite exporter."""
        return SQLiteExporter(self.config.source_connection)
    
    def get_importer(self):
        """Get Spanner importer."""
        return SpannerImporter(self.config.target_connection)
        
    def get_schema_conversion_prompt(self, source_schema: str, 
                                     csv_summaries: Dict,
                                     prev_schema: Optional[str] = None,
                                     prev_convertor: Optional[str] = None,
                                     error: Optional[str] = None) -> str:
        """Get prompt for Spanner schema conversion."""
        
        prompt = f"""
You are an expert database engineer migrating a database from SQLite to Google Cloud Spanner.
Your goal is to generate a Google Cloud Spanner schema and a Python data conversion script.

SOURCE DATABASE (SQLite):
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
   - Converts SQLite types appropriately:
     - INTEGER -> INT64
     - REAL/FLOAT -> FLOAT64
     - TEXT/VARCHAR -> STRING(MAX) or STRING(N)
     - BLOB -> BYTES(MAX)
     - BOOLEAN/TINYINT(1) -> BOOL
     - DATETIME/TIMESTAMP -> TIMESTAMP
   - **CRITICAL**: Do NOT generate `FOREIGN KEY` constraints. The source data may have integrity issues, so we must allow loose relationships.
   - **CRITICAL**: Do NOT use `INTERLEAVE IN PARENT`. Treat all tables as top-level tables.
   - **CRITICAL**: Every table MUST have a PRIMARY KEY. If SQLite table has none, pick a logical one or add an generated ID.
   - Handling Auto-Increment: Spanner DOES NOT support `AUTOINCREMENT` or `SERIAL`. 
     - You MUST remove `AUTOINCREMENT`.
     - Data is loaded from CSVs which already contain the IDs, so you do NOT need to generate new IDs during load. Just define the column as INT64.
   - Use backticks ` for identifiers.
   - Use backticks ` for identifiers.
   - **IMPORTANT**: Sanitize column names to be valid Spanner identifiers.
     - Spanner identifiers must start with a letter (a-z, A-Z) or underscore (_). They CANNOT start with a number.
     - They must contain only alphanumeric characters and underscores.
     - Example: `2013-14 data` -> `col_2013_14_data` (Prefix with `col_` if it starts with a number)
     - Example: `Column Name` -> `Column_Name`
     - You MUST rename columns in the schema AND the `data_convertor.py` script so CSV headers match.
   
2. Generate a Python data conversion script (`data_convertor.py`) that:
   - Reads CSV files from `source_dir`
   - Transforms data to match Spanner strict types (e.g. converting '1'/'0' to True/False for BOOL).
   - Handles NULLs correctly.
   - Writes cleaned CSVs to `dest_dir`.
   - The script must have a `convert_data(source_dir, dest_dir)` function.
   - **CRITICAL**: The script MUST include an `if __name__ == "__main__":` block that:
     - Imports `sys`.
     - Gets `source_dir = sys.argv[1]` and `dest_dir = sys.argv[2]`.
     - Calls `convert_data(source_dir, dest_dir)`.
     - Does NOT rely on hardcoded paths.

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
- If the error is 'Table not found', check dependency order or table names.
- If 'Foreign key constraint violation', ensure child tables are created after parents (or DB handles it via DDL order) and data is consistent.
- If 'Bool' error, ensure conversion script handles 0/1 -> False/True.
"""

        return prompt
