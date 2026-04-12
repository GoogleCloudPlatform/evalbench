"""BigQuery to PostgreSQL query converter."""

from typing import Dict, List, Optional, Tuple, Any
from .base import QueryConverter, QueryResult


class BigQueryToPGQueryConverter(QueryConverter):
    """Converter for BigQuery Standard SQL to PostgreSQL."""
    
    def __init__(self):
        """Initialize BigQuery to PostgreSQL query converter."""
        super().__init__("bigquery", "postgresql")
        
    def get_conversion_prompt(
        self,
        source_schema: str,
        dest_schema: str,
        source_query: str,
        converted_query: Optional[str] = None,
        source_result: Optional[QueryResult] = None,
        dest_result: Optional[QueryResult] = None,
        attempt: int = 1,
        max_attempts: int = 5,
        attempt_history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """Get prompt for BigQuery to PostgreSQL query conversion."""
        
        prompt = f"""
You are an expert database engineer converting SQL queries from Google Cloud BigQuery Standard SQL to PostgreSQL.

SOURCE SCHEMA (BigQuery Pseudo-SQL):
----------------------------------
{source_schema}

TARGET SCHEMA (PostgreSQL):
--------------------------
{dest_schema}

SOURCE QUERY (BigQuery):
----------------------
{source_query}
"""

        if attempt == 1:
            prompt += """
TASK:
-----
Convert the BigQuery query to PostgreSQL SQL.

Rules:
1. Use PostgreSQL syntax.
2. Use double quotes `"` for table and column names if they contain special characters or are reserved keywords.
3. BigQuery backticks ` should be converted to double quotes " in PostgreSQL.
4. Function Mappings:
   - `TIMESTAMP_TRUNC(..., DAY)` -> `date_trunc('day', ...)`
   - `EXTRACT(YEAR FROM ...)` -> `EXTRACT(YEAR FROM ...)` (mostly compatible)
   - `CURRENT_TIMESTAMP()` -> `now()` or `CURRENT_TIMESTAMP`
   - `CONCAT(a, b)` -> `a || b` or `CONCAT(a, b)` (mostly compatible)
   - `IF(cond, a, b)` -> `CASE WHEN cond THEN a ELSE b END`
   - `SAFE_CAST(x AS type)` -> `CAST(x AS type)` with appropriate error handling or just `CAST`.
5. Data Type Mappings:
   - `INT64` -> `BIGINT`
   - `FLOAT64` -> `DOUBLE PRECISION`
   - `STRING` -> `TEXT`
   - `BOOL` -> `BOOLEAN`
6. Remove any BigQuery-specific dataset/project prefixes if they don't exist in the target schema.
7. Use `NULLS LAST` explicitly in `ORDER BY` if the source query relies on BigQuery's default `NULLS LAST` for `DESC`.

OUTPUT FORMAT:
--------------
Return ONLY a JSON object with:
{
  "converted_query": "the postgresql sql query...",
  "notes": "brief explanation of changes..."
}
"""
        else:
            prompt += f"""
PREVIOUS ATTEMPT {attempt-1} FAILED:
------------------------------
Converted Query: {converted_query}
"""
            if dest_result and dest_result.error:
                prompt += f"Execution Error: {dest_result.error}\n"
            elif source_result and dest_result:
                prompt += f"Source Rows: {source_result.total_rows}, Dest Rows: {dest_result.total_rows}\n"
                prompt += "Results did not match. Ensure column names and data types are correct.\n"

            prompt += """
TASK:
-----
Fix the PostgreSQL query. Ensure it produces the EXACT same result set as the BigQuery query.
Check:
1. Column aliases and ordering.
2. Null handling.
3. Type casting for comparisons.
4. Correct use of PostgreSQL functions.

OUTPUT FORMAT:
--------------
Return ONLY a JSON object with:
{
  "converted_query": "the fixed postgresql sql query...",
  "notes": "what was fixed..."
}
"""

        return prompt

    def compare_results(self, source_result: QueryResult, dest_result: QueryResult) -> Tuple[bool, str]:
        """Compare BigQuery results with PostgreSQL results."""
        if source_result.error or dest_result.error:
            return False, f"Execution error: {source_result.error or dest_result.error}"
            
        if source_result.total_rows != dest_result.total_rows:
            return False, f"Row count mismatch: {source_result.total_rows} vs {dest_result.total_rows}"
            
        if not source_result.rows and not dest_result.rows:
            return True, "Both results empty"

        # Compare values (with some tolerance for type differences)
        for i in range(min(len(source_result.rows), 5)):
            s_row = source_result.rows[i]
            d_row = dest_result.rows[i]
            
            if len(s_row) != len(d_row):
                return False, f"Column count mismatch at row {i}"
                
            for j in range(len(s_row)):
                if not self._values_equal(s_row[j], d_row[j]):
                    return False, f"Value mismatch at row {i}, col {j}: {s_row[j]} vs {d_row[j]}"
                    
        return True, "Row counts match and sample data matches"

    def _values_equal(self, val1: Any, val2: Any) -> bool:
        """Check if two values are equal with some flexibility."""
        if val1 is None or val2 is None:
            return val1 == val2
            
        # Float comparison
        if isinstance(val1, (float, int)) and isinstance(val2, (float, int)):
            return abs(float(val1) - float(val2)) < 1e-6
            
        # String comparison
        return str(val1).strip() == str(val2).strip()
