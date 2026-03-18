"""PostgreSQL to MySQL converter."""

from typing import Dict, Optional, List
from .base import DatabaseConverter, ConversionConfig
from ..exporters.pg_exporter import PostgreSQLExporter
from ..importers.mysql_importer import MySQLImporter


class PGToMySQLConverter(DatabaseConverter):
    """Convert from PostgreSQL to MySQL."""
    
    def __init__(self, config: ConversionConfig):
        """Initialize PostgreSQL to MySQL converter."""
        super().__init__(config)
        if config.source_type != 'postgresql':
            raise ValueError(f"Source type must be 'postgresql', got '{config.source_type}'")
        if config.target_type != 'mysql':
            raise ValueError(f"Target type must be 'mysql', got '{config.target_type}'")
    
    def get_exporter(self):
        """Get PostgreSQL exporter instance."""
        return PostgreSQLExporter(self.config.source_connection)
    
    def get_importer(self):
        """Get MySQL importer instance."""
        return MySQLImporter(
            host=self.config.target_connection['host'],
            port=self.config.target_connection.get('port', '3306'),
            user=self.config.target_connection['user'],
            password=self.config.target_connection['password'],
            database=self.config.target_connection['database']
        )
    
    def get_schema_conversion_prompt(
        self,
        source_schema: str,
        csv_summaries: Dict,
        prev_schema: Optional[str] = None,
        prev_convertor: Optional[str] = None,
        error: Optional[str] = None,
        history: Optional[List[Dict]] = None
    ) -> str:
        """Build prompt for AI agent to convert PostgreSQL schema to MySQL.
        
        Args:
            source_schema: PostgreSQL schema (CREATE statements)
            csv_summaries: Dictionary of table names to CSV summaries
            prev_schema: Previous MySQL schema (if any)
            prev_convertor: Previous data convertor script (if any)
            error: Error from previous attempt (if any)
            history: List of previous attempts with errors
            
        Returns:
            Prompt string for AI agent
        """
        # Format CSV summaries with max column lengths prominently displayed
        csv_summary_parts = []
        for table, summary in csv_summaries.items():
            part = f"Table: {table}\n"
            part += f"Columns: {', '.join(summary.get('columns', []))}\n"
            part += f"Total rows: {summary.get('total_rows', 0)}\n"
            
            # Show max lengths prominently
            if 'max_lengths' in summary:
                part += "\n⚠️ MAX COLUMN LENGTHS (USE THESE FOR VARCHAR SIZING!):\n"
                max_lens = summary['max_lengths']
                for col, length in sorted(max_lens.items(), key=lambda x: -x[1])[:20]:  # Top 20 longest
                    if length > 0:
                        part += f"  {col}: {length} chars\n"
            
            # Show sample data
            if summary.get('first_lines'):
                part += f"\nFirst {len(summary['first_lines'])} rows:\n"
                for row in summary['first_lines']:
                    part += f"  {row}\n"
            
            csv_summary_parts.append(part)
        
        csv_summary_text = "\n\n".join(csv_summary_parts)
        
        prompt = rf"""You are a database conversion expert. Convert this PostgreSQL database to MySQL.

===== SOURCE POSTGRESQL SCHEMA =====
{source_schema}

===== CSV DATA SUMMARY =====
{csv_summary_text}

===== CONVERSION RULES =====

**Data Types:**
- SERIAL → INT AUTO_INCREMENT
- BIGSERIAL → BIGINT AUTO_INCREMENT
- TEXT → TEXT (⚠️ ALWAYS use TEXT, never VARCHAR — the CSV sample only shows a subset of rows; real data often has values much longer than the sample maximum)
- CHARACTER VARYING(n) / VARCHAR(n) → Use TEXT unless n <= 50 AND the column is clearly a short fixed-width field (e.g. country code, state code, boolean flag, enum). When in doubt, use TEXT.
  * ⚠️ NEVER use VARCHAR for: addresses, names, descriptions, titles, URLs, notes, comments, or any free-text field — use TEXT
  * VARCHAR is only appropriate for: short codes (VARCHAR(10)), IDs (VARCHAR(50)), or columns where PG explicitly constrains to ≤50 chars AND sample data confirms short values
- INTEGER → INT
- BIGINT → BIGINT
- SMALLINT → SMALLINT
- DOUBLE PRECISION → DOUBLE
- NUMERIC/DECIMAL → DECIMAL
- BOOLEAN → TINYINT(1)
- DATE → DATE
- TIMESTAMP → DATETIME
- BYTEA → BLOB
- JSONB/JSON → LONGTEXT (⚠️ NEVER use MySQL JSON type - it has strict validation and rejects many real-world jsonb values; always use LONGTEXT to safely store all JSON strings)

**NULL Representation:**
- PostgreSQL CSV exports may contain empty strings for NULL
- MySQL uses empty string as NULL for nullable columns
- No special conversion needed

**Boolean Conversion:**
- PostgreSQL: t/f or true/false
- MySQL: 1/0 for TINYINT(1)
- Convert in data_convertor.py

**Identifier Quoting:**
- PostgreSQL uses double quotes: "column_name"
- MySQL uses backticks: `column_name`
- Convert all identifiers

**Foreign Keys:**
- Maintain all foreign key relationships
- Convert table/column names to MySQL style

**CRITICAL PATTERNS (LEARNED FROM SUCCESSFUL MIGRATIONS):**

1. **NULL HANDLING** (⚠️ Critical for DATE, DATETIME, INT columns!):
   PostgreSQL CSV exports use `\\N` (backslash-N) as the NULL marker.
   ONLY treat `\\N` as NULL. Do NOT treat 'NA', 'N/A', 'NULL', 'null' etc. as NULL
   — these are legitimate string values that may appear in text/name columns!
   MUST clean cells in data_convertor.py:
   ```python
   # Clean each cell - handle PostgreSQL NULL marker ONLY
   cell = str(cell).strip()
   # ONLY convert PostgreSQL's actual NULL marker (\\N) to empty string
   # WARNING: Do NOT treat 'NA', 'N/A', 'NULL', 'null' as NULL - they are valid data!
   if cell in ('\\N', '\\\\N'):
       cell = ''
   
   # Write empty string for NULL (MySQL importer treats '' as NULL)
   processed_row.append('' if cell == '' else cell)
   ```

1. **VARCHAR vs TEXT** (⚠️ Most common failure!):
   The CSV summaries show MAX COLUMN LENGTHS for every column.
   
   **MANDATORY RULE**: 
   - NEVER try to guess or hardcode a VARCHAR length like VARCHAR(255) if the max length is unknown or large!
   - For ANY text/string column where the "MAX COLUMN LENGTH" is missing, > 200, or "> 100000", you MUST use `TEXT` or `LONGTEXT` instead of VARCHAR.
   - For short IDs or codes where max length is explicitly < 100, use VARCHAR(max_length + 50).
   - If the column looks like a description, long text, JSON, or medication info, DEFAULT to `TEXT`.
   - Examples:
     * Max length = 16 chars → Use VARCHAR(64)
     * Max length = 89 chars → Use VARCHAR(150)
     * Max length = 560 chars → Use `TEXT`
     * Max length is unknown or missing → Use `TEXT`
   
   **Default safe sizes** (if no data or all NULL):
   - Short text fields (like state codes) → VARCHAR(50)
   - Everything else (names, descriptions, text blobs) → `TEXT`
   
   ⚠️ NEVER use VARCHAR(255) for long text fields like `patient_medication_information`, descriptions, URLs, or JSON! Use `TEXT`!

3. **BOOLEAN CONVERSION**:
   ```python
   # In data_convertor.py, for BOOLEAN/TINYINT(1) columns:
   if cell.lower() in ('t', 'true', '1', 'yes'):
       cell = '1'
   elif cell.lower() in ('f', 'false', '0', 'no', ''):
       cell = '0'
   ```

**data_convertor Requirements:**
- Accept TWO positional command-line arguments: source_dir and output_dir
  Usage: python3 data_convertor.py <source_dir> <output_dir>
- CRITICAL: At the very top of the script (after `import csv` and `import sys`), ALWAYS add:
  ```python
  csv.field_size_limit(sys.maxsize)
  ```
  This is REQUIRED because geometry/WKT columns can contain hundreds of KB per cell. Without this, the script will crash with `_csv.Error: field larger than field limit (131072)`.
- CRITICAL: Always open input CSV files with `newline=''` (e.g., `open(path, 'r', encoding='utf-8', newline='')`). This is REQUIRED by Python's csv module to correctly handle embedded newlines inside quoted fields. Without this, records with multi-line text fields will be corrupted.
- Read all CSV files from source directory
- Convert data for MySQL compatibility:
  * Handle NULL values (empty strings → NULL for numeric/date types)
  * Convert boolean values (t/true → 1, f/false → 0 for TINYINT)
  * Ensure date formats are YYYY-MM-DD
  * Ensure datetime formats are YYYY-MM-DD HH:MM:SS
  * Validate VARCHAR lengths match schema
- Write converted CSVs to output directory
- Print progress and any warnings

**IMPORTANT: Python Syntax**
- When writing Python code that mentions backslash-N, use raw strings r"\\N" or escape it properly
- Do NOT write backslash-N directly in docstrings - Python will raise SyntaxError
- Example: Use cell.replace(r'\\N', '') or cell.replace('\\\\N', '')
"""
        
        # Add previous attempts history if available
        if history:
            prompt += "\n===== PREVIOUS ATTEMPTS HISTORY ====="
            for i, attempt_data in enumerate(history):
                prompt += f"\n--- Attempt {i+1} ---"
                if 'error' in attempt_data and attempt_data['error']:
                    prompt += f"\nPrevious Error:\n{attempt_data['error']}"
                if 'mysql_schema' in attempt_data and attempt_data['mysql_schema']:
                    prompt += f"\nPrevious MySQL Schema:\n{attempt_data['mysql_schema'][:500]}..."
                if 'data_convertor' in attempt_data and attempt_data['data_convertor']:
                    prompt += f"\nPrevious Data Convertor:\n{attempt_data['data_convertor'][:500]}..."
            prompt += "\n\n"
        
        # Add current error if any
        if error:
            prompt += f"""
===== PIPELINE ERROR FROM PREVIOUS ATTEMPT =====
{error}

Please analyze the error and fix it in this attempt.
"""
        
        if prev_schema:
            prompt += f"""
===== PREVIOUS MYSQL SCHEMA =====
{prev_schema}
"""
        
        if prev_convertor:
            prompt += f"""
===== PREVIOUS DATA CONVERTOR =====
{prev_convertor}
"""
        
        prompt += """

===== SCHEMA CONSTRAINTS RULES =====

**NOT NULL constraints:**
- ONLY add NOT NULL to PRIMARY KEY columns
- Do NOT add NOT NULL to any other columns, even if the source PG schema has NOT NULL
- Reason: CSV export converts NULL and empty strings both to '', and the importer may convert '' to NULL, which would violate NOT NULL constraints during loading

**PRIMARY KEY:**
- ONLY create PRIMARY KEY if the source PG table has a primary key
- Do NOT invent or guess primary keys based on column names
- If the PG table has no primary key, do not add one in MySQL

**Do NOT add extra columns (⚠️ CRITICAL):**
- NEVER add a surrogate `id` AUTO_INCREMENT column or ANY other column that does not exist in the source PostgreSQL schema
- Map the original PostgreSQL primary key columns DIRECTLY to MySQL PRIMARY KEY using appropriate MySQL types (INT, BIGINT, VARCHAR, etc.)
- The MySQL table must have EXACTLY the same columns as the PostgreSQL table — no more, no fewer
- Wrong example: adding `id BIGINT AUTO_INCREMENT PRIMARY KEY` when the source has `circuitId INT` as PK
- Correct example: `circuitId INT NOT NULL, PRIMARY KEY (circuitId)`

**Do NOT deduplicate in data_convertor.py (⚠️ CRITICAL):**
- The data_convertor.py must NOT contain any deduplication logic (no `seen_keys`, no `processed_ids` sets, no duplicate-row skipping)
- Import ALL rows exactly as they appear in the source CSV
- Row count in output CSV must equal row count in source CSV (excluding header)

**Geometry / WKT columns:**
- Any column containing WKT geometry data (MULTIPOLYGON, POLYGON, POINT, LINESTRING, GEOMETRYCOLLECTION, etc.) must be typed as LONGTEXT in MySQL
- Do NOT use MySQL GEOMETRY or spatial types — they have strict format requirements and reject many real-world WKT strings

===== YOUR TASK =====
Generate TWO files in JSON format:

{
  "mysql_schema": "Full MySQL CREATE TABLE statements with all columns, data types, primary keys, and foreign keys",
  "data_convertor": "Complete Python script that takes source_dir and output_dir as positional arguments"
}

**CRITICAL**: Use the MAX COLUMN LENGTHS from CSV summaries to set VARCHAR sizes. Do NOT guess!
**CRITICAL**: Handle all NULL representations in data_convertor.py
**CRITICAL**: Convert PostgreSQL booleans (t/f) to MySQL (1/0)
**CRITICAL**: Do NOT add extra columns. Do NOT deduplicate rows.
"""
        
        return prompt

