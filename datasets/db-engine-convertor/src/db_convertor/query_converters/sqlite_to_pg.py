"""SQLite to PostgreSQL query converter."""

from typing import Optional, Tuple, List, Dict
from .base import QueryConverter, QueryResult


class SQLiteToPGQueryConverter(QueryConverter):
    """Convert queries from SQLite to PostgreSQL dialect."""
    
    def __init__(self):
        """Initialize SQLite to PostgreSQL query converter."""
        super().__init__("sqlite", "postgresql")
    
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
        """Build ReAct-style prompt for SQLite to PostgreSQL query conversion."""
        
        prompt = f"""You are an expert SQL query converter (ReAct agent) specializing in SQLite to PostgreSQL migrations.

**YOUR GOAL:** Convert a SQLite query to PostgreSQL and ensure results are SEMANTICALLY EQUIVALENT.

**MATCHING CRITERIA (for result_matched):**
- Same number of rows
- Same data values (meaning-wise equivalent)
- Allow precision differences (e.g., 0.5 vs 0.500000)
- Allow type differences (e.g., INTEGER vs DOUBLE PRECISION) if values match

**CONVERSION RULES FOR POSTGRESQL:**

1. **Column and Table Names**: MUST use double quotes
   - SQLite: `Table`.`Column Name` → PostgreSQL: "Table"."Column Name"

2. **Type Casting**: Use PostgreSQL casting
   - SQLite: CAST(x AS REAL) → PostgreSQL: x::DOUBLE PRECISION

3. **NULL Handling**: PostgreSQL requires explicit NULLS FIRST/LAST in ORDER BY
   - SQLite: ORDER BY column ASC → PostgreSQL: ORDER BY column ASC NULLS LAST

4. **Division**: PostgreSQL integer division returns integer
   - SQLite: col1 / col2 (auto-converts to REAL)
   - PostgreSQL: col1::DOUBLE PRECISION / col2 (for real division)

5. **Boolean Values**: 
   - SQLite: 0/1 → PostgreSQL: Use explicit comparison (= 1, = 0)

===== SCHEMAS =====

SOURCE (SQLite):
{source_schema}

DESTINATION (PostgreSQL):
{dest_schema}

===== SOURCE QUERY (SQLite) =====
{source_query}
"""

        # Add current state information
        if source_result:
            prompt += f"""
===== SOURCE EXECUTION RESULT =====
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
===== DESTINATION EXECUTION RESULT =====
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

**OUTPUT FORMAT (JSON only, no markdown):**

Option 1 - Results match:
{{
  "conversion_finished": "result_matched",
  "notes": "Explain why results match (e.g., same rows, data matches meaning-wise)"
}}

Option 2 - Try new conversion:
{{
  "converted_query": "SELECT ...",
  "notes": "Explain what you're fixing/trying (e.g., fixing NULL ordering, adding type cast)"
}}

Option 3 - Cannot match:
{{
  "conversion_finished": "unable_to_match",
  "notes": "Explain why impossible (e.g., schema mismatch, data not available)"
}}

**CRITICAL REMINDERS:**
1. Your #1 priority is "result_matched". Try VERY HARD to fix any issues!
2. Review ALL previous attempts above to avoid repeating the same failed conversions.
3. If you keep getting the same error, try a DIFFERENT approach (e.g., use CTE, subquery, different casting).
4. Precision/type differences are OK if data meaning is the same.

Output your decision now:
"""
        
        return prompt
    
    def compare_results(
        self,
        source_result: QueryResult,
        dest_result: QueryResult
    ) -> Tuple[bool, str]:
        """Compare query results from SQLite and PostgreSQL.
        
        Comparison criteria:
        - Total rows must match
        - Data should be the same (allowing for precision differences)
        - Column count should match
        
        Args:
            source_result: Result from SQLite query
            dest_result: Result from PostgreSQL query
            
        Returns:
            Tuple of (matches: bool, reason: str)
        """
        # Check for errors
        if source_result.error:
            return False, f"Source query error: {source_result.error}"
        
        if dest_result.error:
            return False, f"Destination query error: {dest_result.error}"
        
        # Check total rows
        if source_result.total_rows != dest_result.total_rows:
            return False, f"Row count mismatch: source={source_result.total_rows}, dest={dest_result.total_rows}"
        
        # Check column count
        if len(source_result.columns) != len(dest_result.columns):
            return False, f"Column count mismatch: source={len(source_result.columns)}, dest={len(dest_result.columns)}"
        
        # Check data
        if len(source_result.rows) != len(dest_result.rows):
            return False, f"Data row mismatch: source={len(source_result.rows)}, dest={len(dest_result.rows)}"
        
        # Compare actual data (allowing for precision differences)
        for i, (src_row, dest_row) in enumerate(zip(source_result.rows, dest_result.rows)):
            if len(src_row) != len(dest_row):
                return False, f"Row {i+1} column count mismatch"
            
            for j, (src_val, dest_val) in enumerate(zip(src_row, dest_row)):
                if not self._values_equal(src_val, dest_val):
                    return False, f"Row {i+1}, column {j+1} mismatch: source={src_val}, dest={dest_val}"
        
        return True, "All checks passed: row counts match, column counts match, data matches"
    
    def _values_equal(self, val1, val2, epsilon=1e-9) -> bool:
        """Compare two values allowing for precision differences.
        
        Args:
            val1: First value
            val2: Second value
            epsilon: Tolerance for floating point comparison
            
        Returns:
            True if values are considered equal
        """
        # Handle None/NULL
        if val1 is None and val2 is None:
            return True
        if val1 is None or val2 is None:
            return False
        
        # Convert to strings for comparison
        str1 = str(val1).strip()
        str2 = str(val2).strip()
        
        # Try exact string match first
        if str1 == str2:
            return True
        
        # Try numeric comparison with epsilon
        try:
            num1 = float(str1)
            num2 = float(str2)
            return abs(num1 - num2) < epsilon
        except (ValueError, TypeError):
            pass
        
        # Case-insensitive string comparison
        if str1.lower() == str2.lower():
            return True
        
        return False
