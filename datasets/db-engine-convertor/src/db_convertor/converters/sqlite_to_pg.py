"""SQLite to PostgreSQL converter."""

from typing import Dict, Optional
from .base import DatabaseConverter, ConversionConfig
from ..exporters.sqlite_exporter import SQLiteExporter


class SQLiteToPGConverter(DatabaseConverter):
    """Converter for SQLite to PostgreSQL migrations."""
    
    def __init__(self, config: ConversionConfig):
        """Initialize SQLite to PostgreSQL converter."""
        super().__init__(config)
        if config.source_type != 'sqlite':
            raise ValueError(f"Source type must be 'sqlite', got '{config.source_type}'")
        if config.target_type != 'postgresql':
            raise ValueError(f"Target type must be 'postgresql', got '{config.target_type}'")
    
    def get_exporter(self):
        """Get SQLite exporter."""
        return SQLiteExporter(self.config.source_connection)
    
    def get_importer(self):
        """Get PostgreSQL importer."""
        from ..importers.pg_importer import PostgreSQLImporter
        return PostgreSQLImporter(self.config.target_connection)
    
    def get_schema_conversion_prompt(self, source_schema: str, 
                                     csv_summaries: Dict, 
                                     prev_schema: Optional[str] = None,
                                     prev_convertor: Optional[str] = None,
                                     error: Optional[str] = None) -> str:
        """Build prompt for SQLite to PostgreSQL schema conversion."""
        
        prompt = """You are an expert database migration agent. Your task is to convert a SQLite database to PostgreSQL.

You will receive:
1. SQLite table creation statements
2. Summary of CSV data files (first 5 and last 5 rows, column names, row counts)
3. Previously generated PostgreSQL schema (if any)
4. Previously generated data_convertor.py script (if any)
5. Pipeline execution error (if any)

Your job is to output TWO files:
1. **postgresql_schema.sql**: PostgreSQL table creation statements
2. **data_convertor.py**: Python script to convert SQLite CSV data to PostgreSQL-compatible CSV

IMPORTANT CONVERSION RULES FOR postgresql_schema.sql:
- Convert SQLite types to appropriate PostgreSQL types:
  * TEXT -> VARCHAR or TEXT
  * INTEGER -> INTEGER or BIGINT
  * REAL -> DOUBLE PRECISION or NUMERIC
  * BLOB -> BYTEA
  * DATE -> DATE
- Handle NULL values properly
- Convert foreign key syntax to PostgreSQL format
- Ensure column names are properly quoted if they contain special characters

CRITICAL REQUIREMENTS FOR data_convertor.py:
- MUST accept TWO command-line arguments: <source_dir> <output_dir>
  Example: python3 data_convertor.py ./source_csvs ./converted_csvs
- MUST read CSV files from the source_dir argument (sys.argv[1])
- MUST write converted CSV files to the output_dir argument (sys.argv[2])
- MUST use the exact same filenames (table_name.csv) for input and output
- Handle any data type conversions needed (dates, booleans, empty strings to NULL, etc.)
- Convert SQLite integer booleans (0/1) to PostgreSQL boolean format (f/t) if needed
- Make sure data precision matches PostgreSQL requirements
- The converted CSVs must be compatible with PostgreSQL's COPY command with CSV format

OUTPUT FORMAT:
You must output a JSON object with this exact structure:
{
  "postgresql_schema": "-- Full PostgreSQL schema here\\nCREATE TABLE ...",
  "data_convertor": "#!/usr/bin/env python3\\n# Full Python script here"
}

=== SQLITE SCHEMA ===
"""
        
        prompt += source_schema
        
        prompt += "\n\n=== CSV DATA SUMMARIES ==="
        prompt += self._format_csv_summaries(csv_summaries)
        
        if prev_schema:
            prompt += "\n\n=== PREVIOUS POSTGRESQL SCHEMA (with line numbers) ==="
            prompt += "\n" + self._read_file_with_line_numbers(prev_schema)
        
        if prev_convertor:
            prompt += "\n\n=== PREVIOUS DATA CONVERTOR SCRIPT (with line numbers) ==="
            prompt += "\n" + self._read_file_with_line_numbers(prev_convertor)
        
        if error:
            prompt += "\n\n=== PIPELINE EXECUTION ERROR ==="
            prompt += "\n" + error
            prompt += "\n\nPlease fix the errors in the schema or convertor script."
        
        prompt += """

Now generate the corrected or improved postgresql_schema.sql and data_convertor.py.
Remember to output valid JSON with "postgresql_schema" and "data_convertor" keys.
"""
        
        return prompt
    
    def _format_csv_summaries(self, csv_summaries: Dict) -> str:
        """Format CSV summaries for the prompt."""
        output = []
        for table_name, summary in csv_summaries.items():
            output.append(f"\n=== Table: {table_name} ===")
            output.append(f"Columns: {', '.join(summary['columns'])}")
            output.append(f"Total rows: {summary['total_rows']}")
            
            if summary['first_lines']:
                output.append("\nFirst 5 rows:")
                for i, row in enumerate(summary['first_lines'], 1):
                    output.append(f"  {i}: {row}")
            
            if summary['last_lines'] and len(summary['last_lines']) > 0:
                output.append("\nLast 5 rows:")
                for i, row in enumerate(summary['last_lines'], 1):
                    output.append(f"  {i}: {row}")
        
        return '\n'.join(output)
    
    def _read_file_with_line_numbers(self, file_path: str) -> str:
        """Read file with line numbers (nl -a format)."""
        import os
        if not os.path.exists(file_path):
            return "File not found"
        
        with open(file_path, 'r', encoding='utf-8') as f:
            lines = f.readlines()
        
        numbered_lines = []
        for i, line in enumerate(lines, 1):
            numbered_lines.append(f"{i:6d}\t{line.rstrip()}")
        
        return '\n'.join(numbered_lines)

