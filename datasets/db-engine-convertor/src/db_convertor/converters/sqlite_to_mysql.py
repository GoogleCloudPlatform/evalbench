"""SQLite to MySQL converter."""

from typing import Dict, Optional
from .base import DatabaseConverter, ConversionConfig
from ..exporters.sqlite_exporter import SQLiteExporter
from ..importers.mysql_importer import MySQLImporter


class SQLiteToMySQLConverter(DatabaseConverter):
    """Convert from SQLite to MySQL."""
    
    def __init__(self, config: ConversionConfig):
        """Initialize SQLite to MySQL converter."""
        super().__init__(config)
        if config.source_type != 'sqlite':
            raise ValueError(f"Source type must be 'sqlite', got '{config.source_type}'")
        if config.target_type != 'mysql':
            raise ValueError(f"Target type must be 'mysql', got '{config.target_type}'")
    
    def get_exporter(self):
        """Get SQLite exporter instance."""
        return SQLiteExporter(self.config.source_connection)
    
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
        error: Optional[str] = None
    ) -> str:
        """Build prompt for AI agent to convert SQLite schema to MySQL.
        
        Args:
            source_schema: SQLite schema (CREATE statements)
            csv_summaries: Dictionary of table names to CSV summaries
            prev_schema: Previous MySQL schema (if any)
            prev_convertor: Previous data convertor script (if any)
            error: Error from previous attempt (if any)
            
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
        
        prompt = f"""You are a database conversion expert. Convert this SQLite database to MySQL.

===== SOURCE SQLITE SCHEMA =====
{source_schema}

===== CSV DATA SUMMARY =====
{csv_summary_text}
"""
        
        if error:
            prompt += f"""
===== PREVIOUS ATTEMPT ERROR =====
{error}
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
===== YOUR TASK =====

Generate TWO outputs to convert this SQLite database to MySQL:

1. **mysql_schema**: Full MySQL table creation statements
2. **data_convertor**: Python script to convert CSV files for MySQL

**KEY MYSQL CONVERSION RULES:**

**Data Types:**
- INTEGER → INT or BIGINT (use BIGINT for large IDs)
- REAL → DOUBLE
- TEXT → Use max_lengths from CSV summary to decide:
  * If max_length < 100 → VARCHAR(max * 2) with minimum VARCHAR(50)
  * If max_length < 255 → VARCHAR(max * 1.5) or VARCHAR(255)
  * If max_length > 255 → TEXT
  * If all NULL/empty → VARCHAR(255) default
- BLOB → BLOB
- BOOLEAN → TINYINT(1) or BOOLEAN
- DATE → DATE
- DATETIME → DATETIME

**Syntax Differences:**
- Use backticks for identifiers: `table_name`, `column_name`
- MySQL doesn't have AUTOINCREMENT, use AUTO_INCREMENT
- PRIMARY KEY columns should be NOT NULL
- Use ENGINE=InnoDB for tables with foreign keys
- NULL representation: Use NULL keyword (not \\N)
- Boolean: Use 1/0 for TINYINT(1), or TRUE/FALSE for BOOLEAN type

**data_convertor Requirements:**
- Accept TWO positional command-line arguments: source_dir and output_dir
  Usage: python3 data_convertor.py <source_dir> <output_dir>
- Read all CSV files from source directory
- Convert data for MySQL compatibility:
  * Handle NULL values (empty strings → NULL for numeric/date types)
  * Convert boolean values (empty/0 → 0, 1/true → 1 for TINYINT)
  * Ensure date formats are YYYY-MM-DD
  * Ensure datetime formats are YYYY-MM-DD HH:MM:SS
  * Validate VARCHAR lengths match schema
- Write converted CSVs to output directory
- Print progress and any warnings

**IMPORTANT: Python Syntax**
- When writing Python code that mentions backslash-N, use raw strings r"\\N" or escape it properly
- Do NOT write \\N directly in docstrings - Python will raise SyntaxError
- Example: Use cell.replace(r'\\N', '') or cell.replace('\\\\N', '')

**CRITICAL PATTERNS (LEARNED FROM SUCCESSFUL MIGRATIONS):**

1. **NULL HANDLING** (⚠️ Critical for DATE, DATETIME, INT columns!):
   CSV files may contain various NULL representations that MySQL rejects.
   MUST clean all cells in data_convertor.py:
   ```python
   # Clean each cell - handle all NULL representations
   cell = str(cell).strip()
   # Replace common NULL representations
   if cell in ('', 'NULL', 'null', '\\N', 'N/A', 'NA'):
       cell = ''
   # For backslash-N (common in SQLite exports)
   cell = cell.replace('\\\\N', '').replace('\\N', '')
   
   # Write empty string for NULL (MySQL interprets as NULL)
   processed_row.append('' if cell == '' else cell)
   ```

2. **VARCHAR LENGTH** (⚠️ Most common failure!):
   The CSV summaries show MAX COLUMN LENGTHS for every column.
   
   **MANDATORY RULE**: 
   - For VARCHAR columns, look at "MAX COLUMN LENGTHS" section in CSV summary
   - Add 50-100% buffer to max length for safety
   - Examples:
     * Max length = 16 chars → Use VARCHAR(30) or VARCHAR(50)
     * Max length = 89 chars → Use VARCHAR(150) or VARCHAR(200)
     * Max length = 5 chars → Use VARCHAR(10) minimum
   
   **Default safe sizes** (if no data or all NULL):
   - Short text fields → VARCHAR(50)
   - Names, codes → VARCHAR(100)
   - Descriptions, URLs → VARCHAR(255) or TEXT
   
   ⚠️ NEVER use VARCHAR(2) or VARCHAR(10) without checking actual max length!

3. **SCHEMA VALIDATION**:
   - Check CSV summaries for actual VARCHAR max lengths before setting schema
   - Foreign key columns must match exactly (length and type)

**OUTPUT FORMAT (JSON only, no markdown):**
{
  "mysql_schema": "-- Full MySQL schema here...",
  "data_convertor": "#!/usr/bin/env python3\\n# Full script here..."
}

Output your conversion now:
"""
        
        return prompt

