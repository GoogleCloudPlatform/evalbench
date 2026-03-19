"""PostgreSQL to BigQuery query converter."""

from typing import Dict, List, Optional, Tuple, Any
from .base import QueryConverter, QueryResult


class PostgreSQLToBigQueryQueryConverter(QueryConverter):
    """Converter for PostgreSQL queries to BigQuery SQL."""
    
    def __init__(self):
        """Initialize PostgreSQL to BigQuery query converter."""
        super().__init__("postgresql", "bigquery")
        
    def get_conversion_prompt(
        self,
        source_schema: str,
        dest_schema: str,
        source_query: str,
        converted_query: Optional[str] = None,
        source_result: Optional[QueryResult] = None,
        dest_result: Optional[QueryResult] = None,
        attempt: int = 1,
        max_attempts: int = 3,
        attempt_history: Optional[List[Dict[str, Any]]] = None
    ) -> str:
        """Get prompt for BigQuery query conversion."""
        
        prompt = f"""
You are an expert database engineer converting SQL queries from PostgreSQL to Google Cloud BigQuery.

SOURCE SCHEMA (PostgreSQL):
-------------------------
{source_schema}

TARGET SCHEMA (BigQuery):
-------------------------
{dest_schema}

SOURCE QUERY (PostgreSQL):
------------------------
{source_query}
"""

        if attempt == 1:
            prompt += """
TASK:
-----
Convert the PostgreSQL query to BigQuery Standard SQL.
Rules:
1. Use BigQuery Standard SQL syntax.
2. Use backticks ` for table and column names if they contain spaces, special characters, or are reserved keywords.
3. PostgreSQL double quotes " should generally be converted to backticks ` in BigQuery.
4. Data Type Mappings:
   - BIGINT -> INT64
   - DOUBLE PRECISION -> FLOAT64
   - TEXT/VARCHAR -> STRING
   - TIMESTAMP -> TIMESTAMP
5. Common Functions:
   - `now()` -> `CURRENT_TIMESTAMP()`
   - `date_trunc('day', ...)` -> `TIMESTAMP_TRUNC(..., DAY)`
   - `extract(year from ...)` -> `EXTRACT(YEAR FROM ...)`
   - Concatenation: `||` works, but `CONCAT()` is more common.
6. Handle `LIMIT` and `OFFSET` (BigQuery supports them).
7. Handle `::type` casting -> `CAST(... AS type)`.
8. Fully qualify table names if necessary using the project and dataset: `project.dataset.table`. 
   **However**, assume the query will run in a context where the dataset is already set, so just `table` or `dataset.table` is usually sufficient unless cross-dataset. Use the table names as defined in the TARGET SCHEMA.

OUTPUT FORMAT:
--------------
Return ONLY a JSON object with:
{
  "converted_query": "the bigquery sql query...",
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
Fix the BigQuery query. Ensure it produces the EXACT same result set as the PostgreSQL query.
Check:
1. Column aliases and ordering.
2. Null handling.
3. Type casting for comparisons.
4. Correct use of BigQuery functions.

OUTPUT FORMAT:
--------------
Return ONLY a JSON object with:
{
  "converted_query": "the fixed bigquery sql query...",
  "notes": "what was fixed..."
}
"""

        return prompt

    def compare_results(self, source_result: QueryResult, dest_result: QueryResult) -> Tuple[bool, str]:
        """Compare PostgreSQL results with BigQuery results."""
        if source_result.error or dest_result.error:
            return False, f"Execution error: {source_result.error or dest_result.error}"
            
        if source_result.total_rows != dest_result.total_rows:
            return False, f"Row count mismatch: {source_result.total_rows} vs {dest_result.total_rows}"
            
        if not source_result.rows and not dest_result.rows:
            return True, "Both results empty"

        # Compare values (with some tolerance for type differences)
        for i in range(min(len(source_result.rows), 5)):  # Check first 5 rows
            s_row = source_result.rows[i]
            d_row = dest_result.rows[i]
            
            if len(s_row) != len(d_row):
                return False, f"Column count mismatch at row {i}"
                
            for j in range(len(s_row)):
                if not self._values_equal(s_row[j], d_row[j]):
                    return False, f"Value mismatch at row {i}, col {j} ({source_result.columns[j]}): {s_row[j]} vs {d_row[j]}"
                    
        return True, "Row counts match and sample data matches"

    def _values_equal(self, val1: Any, val2: Any) -> bool:
        """Check if two values are equal with some flexibility."""
        if val1 is None or val2 is None:
            return val1 == val2
            
        # Float comparison
        if isinstance(val1, (float, int)) and isinstance(val2, (float, int)):
            return abs(float(val1) - float(val2)) < 1e-6
            
        # String comparison of stringified values
        return str(val1).strip() == str(val2).strip()
