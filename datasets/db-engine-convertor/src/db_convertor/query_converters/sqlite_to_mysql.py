"""SQLite to MySQL query converter."""

from typing import Optional, Tuple, List, Dict
from .base import QueryConverter, QueryResult


class SQLiteToMySQLQueryConverter(QueryConverter):
    """Convert queries from SQLite to MySQL dialect."""
    
    def __init__(self):
        super().__init__("sqlite", "mysql")
    
    def get_conversion_prompt(
        self,
        source_schema: str,
        dest_schema: str,
        source_query: str,
        converted_query: Optional[str] = None,
        source_result: Optional[QueryResult] = None,
        dest_result: Optional[QueryResult] = None,
        attempt: int = 1,
        max_attempts: int = 10,
        attempt_history: Optional[List[Dict]] = None
    ) -> str:
        """Build prompt for SQLite to MySQL query conversion.
        
        Args:
            source_schema: SQLite schema
            dest_schema: MySQL schema
            source_query: Original SQLite query
            converted_query: Previous MySQL query attempt
            source_result: Result from SQLite query
            dest_result: Result from MySQL query
            attempt: Current attempt number
            max_attempts: Maximum number of attempts
            attempt_history: List of previous attempts
            
        Returns:
            Prompt string for AI agent
        """
        prompt = f"""You are an expert in SQL query conversion from SQLite to MySQL.

===== SQLITE SCHEMA =====
{source_schema}

===== MYSQL SCHEMA =====
{dest_schema}

===== SOURCE SQLITE QUERY =====
{source_query}
"""
        
        if source_result:
            prompt += f"""
===== SQLITE EXECUTION RESULT =====
{source_result.get_summary()}
"""
        
        # Show all previous attempts if any
        if attempt_history:
            prompt += "\n===== PREVIOUS ATTEMPTS =====\n"
            for hist in attempt_history:
                prompt += f"\n--- Attempt {hist['attempt']} ---\n"
                prompt += f"Query:\n{hist['query']}\n\n"
                result = hist['result']
                if result.error:
                    prompt += f"Result: ERROR - {result.error}\n"
                else:
                    prompt += f"Result: {result.total_rows} rows\n"
                prompt += f"Notes: {hist['notes']}\n"
            prompt += "\n"
        
        # Show current attempt if any
        if converted_query:
            prompt += f"""
===== CURRENT CONVERSION ATTEMPT =====
{converted_query}
"""
            
            if dest_result:
                prompt += f"""
===== MYSQL EXECUTION RESULT =====
{dest_result.get_summary() if not dest_result.error else f"ERROR: {dest_result.error}"}
"""
                
                if source_result and not dest_result.error:
                    prompt += f"""
===== COMPARISON ANALYSIS =====
- Source rows: {source_result.total_rows}
- Destination rows: {dest_result.total_rows}
"""
                    if source_result.total_rows == dest_result.total_rows:
                        prompt += "✓ Row counts MATCH\n"
                        
                        # Add detailed comparison
                        matches, reason = self.compare_results(source_result, dest_result)
                        if matches:
                            prompt += f"✓ Data MATCHES: {reason}\n"
                        else:
                            prompt += f"✗ Data MISMATCH: {reason}\n"
                    else:
                        prompt += f"✗ Row counts MISMATCH\n"
        
        # Add ReAct-style instructions
        prompt += f"""
===== YOUR TASK (Attempt {attempt}/{max_attempts}) =====

**Read the information above and decide what to do:**

1. If results MATCH (same rows, meaning-wise same data) → Declare SUCCESS with "result_matched"
2. If you need to try a different conversion → Provide a new "converted_query" to test
3. If conversion is impossible (schema issues, data loss) → Declare "unable_to_match"

**KEY SQLITE → MYSQL CONVERSION RULES:**

**Identifier Quoting:**
- SQLite uses backticks (`) or double quotes (")
- MySQL uses backticks (`) for identifiers
- Always use backticks in MySQL: `table_name`, `column_name`

**Data Type Differences:**
- SQLite INTEGER (for booleans) → MySQL TINYINT(1) or BOOLEAN
  - SQLite: WHERE `Charter School (Y/N)` = 1
  - MySQL: WHERE `Charter School (Y/N)` = 1 (if TINYINT)
  - MySQL: WHERE `Charter School (Y/N)` = TRUE (if BOOLEAN)

**String Functions:**
- SQLite: || for concatenation → MySQL: CONCAT()
- SQLite: SUBSTR() → MySQL: SUBSTRING()

**Date/Time:**
- SQLite stores dates as TEXT → MySQL has DATE/DATETIME types
- May need STR_TO_DATE() for conversions

**NULL Handling:**
- Both handle NULL similarly
- Use IS NULL / IS NOT NULL

**Sorting:**
- SQLite: NULLs sorted as smallest values (last in DESC)
- MySQL: NULLs sorted as smallest values (last in DESC) - SAME!
- Usually no ORDER BY changes needed for NULLs

**LIMIT/OFFSET:**
- Both use the same syntax: LIMIT n or LIMIT offset, n

**OUTPUT FORMAT (JSON only, no markdown):**

Option 1 - Results match:
{{
  "conversion_finished": "result_matched",
  "notes": "Explain why results match (e.g., same rows, data matches meaning-wise)"
}}

Option 2 - Try new conversion:
{{
  "converted_query": "SELECT ...",
  "notes": "Explain what you're fixing/trying (e.g., fixing identifier quoting, handling boolean)"
}}

Option 3 - Cannot match:
{{
  "conversion_finished": "unable_to_match",
  "notes": "Explain why impossible (e.g., schema mismatch, data not available)"
}}

**CRITICAL REMINDERS:**
1. Your #1 priority is "result_matched". Try VERY HARD to fix any issues!
2. Review ALL previous attempts above to avoid repeating the same failed conversions.
3. If you keep getting the same error, try a DIFFERENT approach (e.g., use subquery, different function).
4. Precision/type differences are OK if data meaning is the same.

Output your decision now:
"""
        
        return prompt
    
    def compare_results(
        self,
        source_result: QueryResult,
        dest_result: QueryResult
    ) -> Tuple[bool, str]:
        """Compare query results from SQLite and MySQL.
        
        Comparison criteria:
        - Total rows must match
        - Data should be the same (allowing for precision differences)
        
        Args:
            source_result: Result from SQLite query
            dest_result: Result from MySQL query
            
        Returns:
            Tuple of (matches: bool, reason: str)
        """
        # Check row counts
        if source_result.total_rows != dest_result.total_rows:
            return False, f"Row count mismatch: {source_result.total_rows} vs {dest_result.total_rows}"
        
        # Check column counts
        if len(source_result.columns) != len(dest_result.columns):
            return False, f"Column count mismatch: {len(source_result.columns)} vs {len(dest_result.columns)}"
        
        # If no rows, they match
        if source_result.total_rows == 0:
            return True, "Both queries return 0 rows"
        
        # Compare data (allowing for type/precision differences)
        for i, (src_row, dst_row) in enumerate(zip(source_result.rows, dest_result.rows)):
            if len(src_row) != len(dst_row):
                return False, f"Row {i} column count mismatch"
            
            for j, (src_val, dst_val) in enumerate(zip(src_row, dst_row)):
                # Handle None/NULL
                if src_val is None and dst_val is None:
                    continue
                if (src_val is None) != (dst_val is None):
                    return False, f"Row {i}, col {j}: NULL mismatch ({src_val} vs {dst_val})"
                
                # Try numeric comparison (allowing for precision differences)
                try:
                    src_num = float(src_val) if src_val is not None else None
                    dst_num = float(dst_val) if dst_val is not None else None
                    
                    if src_num is not None and dst_num is not None:
                        # Allow small floating point differences
                        if abs(src_num - dst_num) < 1e-6:
                            continue
                except (ValueError, TypeError):
                    # Non-numeric values: fall through to string comparison below
                    pass
                
                # String comparison
                if str(src_val).strip() != str(dst_val).strip():
                    return False, f"Row {i}, col {j}: value mismatch ({src_val} vs {dst_val})"
        
        return True, "All checks passed: row counts match, column counts match, data matches"


