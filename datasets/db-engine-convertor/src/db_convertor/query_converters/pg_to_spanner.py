"""PostgreSQL to Google Cloud Spanner query converter."""

from typing import Optional, List, Dict
from .base import QueryConverter, QueryResult

class PostgreSQLToSpannerQueryConverter(QueryConverter):
    """Convert queries from PostgreSQL to Google Cloud Spanner dialect."""
    
    def __init__(self):
        """Initialize PostgreSQL to Spanner query converter."""
        super().__init__("postgresql", "spanner")
    
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
        """Build ReAct-style prompt for PostgreSQL to Spanner query conversion."""
        
        prompt = f"""You are an expert SQL query converter (ReAct agent) specializing in PostgreSQL to Google Cloud Spanner migrations.

**YOUR GOAL:** Convert a PostgreSQL query to Google Cloud Spanner SQL and ensure results are SEMANTICALLY EQUIVALENT.

**MATCHING CRITERIA (for result_matched):**
- Same number of rows
- Same data values (meaning-wise equivalent)
- Allow precision differences (e.g., 0.5 vs 0.500000)
- Allow type differences (e.g., INT64 vs FLOAT64) if values match numerically
- Allow string/bytes differences if content matches

**CONVERSION RULES FOR SPANNER:**

1. **Identifiers**: MUST use backticks `
   - PostgreSQL: "Table"."Column" → Spanner: `Table`.`Column`

2. **Type Casting**: Use `CAST(x AS TYPE)` or `SAFE_CAST(x AS TYPE)`
   - PostgreSQL: x::type or CAST(x AS type)
   - Spanner: CAST(x AS INT64), CAST(x AS FLOAT64), CAST(x AS STRING)

3. **Date/Time Functions**:
   - PostgreSQL: now() → Spanner: CURRENT_TIMESTAMP()
   - PostgreSQL: CURRENT_DATE → Spanner: CURRENT_DATE()
   - PostgreSQL: EXTRACT(YEAR FROM col) → Spanner: EXTRACT(YEAR FROM col) (Same)
   - PostgreSQL: to_char(col, 'YYYY') → Spanner: FORMAT_TIMESTAMP('%Y', col)

4. **String Functions**:
   - PostgreSQL: char_length(x) → Spanner: LENGTH(x)
   - PostgreSQL: x || y → Spanner: x || y (Same)

5. **JSON Handling**:
   - PostgreSQL: col ->> 'key' → Spanner: JSON_VALUE(col, '$.key') (if col is JSON) 
   - Note: If JSONB was converted to STRING(MAX) in Spanner, use JSON_EXTRACT_SCALAR or similar.

6. **Boolean Values**:
   - PostgreSQL: TRUE/FALSE → Spanner: TRUE/FALSE (Case-insensitive usually, but stick to UPPERCASE)

7. **Limit/Offset**:
   - PostgreSQL: LIMIT n OFFSET m → Spanner: LIMIT n OFFSET m (Same)

8. **Casing**:
   - PostgreSQL identifiers are often lowercase unless quoted. Spanner is case-insensitive but usually follows the schema casing.

===== SCHEMAS =====

SOURCE (PostgreSQL):
{source_schema}

DESTINATION (Spanner):
{dest_schema}

===== SOURCE QUERY (PostgreSQL) =====
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
  "notes": "Explain why results match"
}}

Option 2 - Try new conversion:
{{
  "converted_query": "SELECT ...",
  "notes": "Explain what you're fixing/trying"
}}

Option 3 - Cannot match:
{{
  "conversion_finished": "unable_to_match",
  "notes": "Explain why impossible"
}}

Output your decision now:
"""
        
        return prompt
    
    def compare_results(
        self,
        source_result: QueryResult,
        dest_result: QueryResult
    ) -> tuple[bool, str]:
        """Compare query results from PostgreSQL and Spanner."""
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
        for i, (src_row, dest_row) in enumerate(zip(source_result.rows, dest_result.rows)):
            for j, (src_val, dest_val) in enumerate(zip(src_row, dest_row)):
                if not self._values_equal(src_val, dest_val):
                    return False, f"Row {i+1}, column {j+1} mismatch: source={src_val}, dest={dest_val}"
        
        return True, "All checks passed"
    
    def _values_equal(self, val1, val2, epsilon=1e-9) -> bool:
        """Compare two values allowing for precision differences."""
        if val1 is None and val2 is None:
            return True
        if val1 is None or val2 is None:
            return False
            
        str1 = str(val1).strip()
        str2 = str(val2).strip()
        
        if str1 == str2:
            return True
            
        try:
            num1 = float(str1)
            num2 = float(str2)
            return abs(num1 - num2) < epsilon
        except (ValueError, TypeError):
            pass
            
        if str1.lower() == str2.lower():
            return True
            
        return False
