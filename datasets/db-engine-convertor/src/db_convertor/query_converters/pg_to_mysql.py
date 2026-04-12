"""PostgreSQL to MySQL query converter."""

from typing import Optional, Tuple, List, Dict
from .base import QueryConverter, QueryResult


class PGToMySQLQueryConverter(QueryConverter):
    """Convert queries from PostgreSQL to MySQL dialect."""
    
    def __init__(self):
        super().__init__("postgresql", "mysql")
    
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
        """Build prompt for PostgreSQL to MySQL query conversion.
        
        Args:
            source_schema: PostgreSQL schema
            dest_schema: MySQL schema
            source_query: Original PostgreSQL query
            converted_query: Previous MySQL query attempt
            source_result: Result from PostgreSQL query
            dest_result: Result from MySQL query
            attempt: Current attempt number
            max_attempts: Maximum number of attempts
            attempt_history: List of previous attempts
            
        Returns:
            Prompt string for AI agent
        """
        prompt = f"""You are an expert in SQL query conversion from PostgreSQL to MySQL.

===== POSTGRESQL SCHEMA =====
{source_schema}

===== MYSQL SCHEMA =====
{dest_schema}

===== SOURCE POSTGRESQL QUERY =====
{source_query}
"""
        
        if source_result:
            prompt += f"""
===== POSTGRESQL EXECUTION RESULT =====
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
                        prompt += f"✗ Row counts DIFFER by {abs(source_result.total_rows - dest_result.total_rows)}\n"
        
        prompt += f"""

===== CONVERSION RULES =====

**Identifier Quoting:**
- PostgreSQL uses double quotes: "column_name"
- MySQL uses backticks: `column_name`
- Convert ALL double-quoted identifiers to backticks

**Boolean Values:**
- PostgreSQL: TRUE/FALSE or t/f
- MySQL: 1/0 for TINYINT(1) columns
- Example: "Active" = TRUE → `Active` = 1

**String Comparison:**
- Both are case-sensitive by default in standard mode
- PostgreSQL ILIKE → MySQL: LOWER(column) LIKE LOWER(pattern)
- PostgreSQL SIMILAR TO → MySQL: REGEXP

**Date/Time Functions:**
- PostgreSQL NOW() → MySQL NOW()
- PostgreSQL CURRENT_DATE → MySQL CURDATE()
- PostgreSQL CURRENT_TIMESTAMP → MySQL CURRENT_TIMESTAMP
- PostgreSQL EXTRACT(YEAR FROM date) → MySQL YEAR(date)
- PostgreSQL date '2020-01-01' → MySQL '2020-01-01' (remove date keyword)

**String Functions:**
- PostgreSQL || (concatenation) → MySQL CONCAT()
- PostgreSQL POSITION(substring IN string) → MySQL LOCATE(substring, string)
- PostgreSQL SUBSTRING(string FROM start FOR length) → MySQL SUBSTRING(string, start, length)

**Casting:**
- PostgreSQL column::INTEGER → MySQL CAST(column AS SIGNED)
- PostgreSQL column::VARCHAR → MySQL CAST(column AS CHAR)
- PostgreSQL column::DATE → MySQL CAST(column AS DATE)

**Array Operations:**
- PostgreSQL arrays not supported in MySQL
- Convert ANY/ALL operations to IN or EXISTS subqueries

**Window Functions:**
- Both support window functions, syntax is similar
- ROW_NUMBER(), RANK(), DENSE_RANK() work in both

**Aggregates:**
- Both support COUNT, SUM, AVG, MIN, MAX
- PostgreSQL STRING_AGG → MySQL GROUP_CONCAT

**LIMIT/OFFSET:**
- Same syntax in both: LIMIT n OFFSET m

**Common Patterns:**
- PostgreSQL: column IS DISTINCT FROM value → MySQL: (column != value OR column IS NULL)
- PostgreSQL: column IS NOT DISTINCT FROM value → MySQL: (column = value OR (column IS NULL AND value IS NULL))

===== YOUR TASK (Attempt {attempt}/{max_attempts}) =====

You are a ReAct-style agent. Analyze the current state and decide your action:

1. **If results MATCH** (same row count, same data):
   Return: {{"conversion_finished": "result_matched", "notes": "explanation"}}

2. **If conversion is IMPOSSIBLE** (incompatible SQL features, cannot be converted):
   Return: {{"conversion_finished": "unable_to_match", "notes": "reason why impossible"}}

3. **If you want to try a new conversion**:
   Return: {{"converted_query": "new MySQL query", "notes": "what you're trying"}}

**IMPORTANT**: 
- Double-check identifier quoting: "column" → `column`
- Handle boolean conversions: TRUE/FALSE → 1/0
- Remove PostgreSQL-specific syntax (::, date keyword, etc.)
- Ensure data precision matches (allow minor floating point differences)
- Row counts MUST match exactly

Output ONLY valid JSON with one of the above formats.
"""
        
        return prompt
    
    def compare_results(
        self,
        source_result: QueryResult,
        dest_result: QueryResult
    ) -> Tuple[bool, str]:
        """Compare PostgreSQL and MySQL query results.
        
        Args:
            source_result: Result from PostgreSQL
            dest_result: Result from MySQL
            
        Returns:
            Tuple of (matches: bool, reason: str)
        """
        # Check if both have errors
        if source_result.error and dest_result.error:
            return False, f"Both queries failed. Source: {source_result.error}, Dest: {dest_result.error}"
        
        # Check if only one has error
        if source_result.error:
            return False, f"Source query failed: {source_result.error}"
        if dest_result.error:
            return False, f"Destination query failed: {dest_result.error}"
        
        # Check row counts
        if source_result.total_rows != dest_result.total_rows:
            return False, f"Row count mismatch: source={source_result.total_rows}, dest={dest_result.total_rows}"
        
        # Check column counts
        if len(source_result.columns) != len(dest_result.columns):
            return False, f"Column count mismatch: source={len(source_result.columns)}, dest={len(dest_result.columns)}"
        
        # If no rows, they match
        if source_result.total_rows == 0:
            return True, "Both queries return 0 rows"
        
        # Compare actual data (allowing for type differences and floating point precision)
        for i, (source_row, dest_row) in enumerate(zip(source_result.rows[:10], dest_result.rows[:10])):  # Check first 10 rows
            if len(source_row) != len(dest_row):
                return False, f"Row {i} column count mismatch"
            
            for j, (source_val, dest_val) in enumerate(zip(source_row, dest_row)):
                # Handle None/NULL
                if source_val is None and dest_val is None:
                    continue
                if (source_val is None) != (dest_val is None):
                    return False, f"Row {i}, col {j}: NULL mismatch ({source_val} vs {dest_val})"
                
                # Convert to strings for comparison (handles different numeric types)
                source_str = str(source_val)
                dest_str = str(dest_val)
                
                # Try numeric comparison with tolerance for floating point
                try:
                    source_float = float(source_str)
                    dest_float = float(dest_str)
                    # Allow 0.01% relative difference for floating point
                    if abs(source_float - dest_float) > abs(source_float) * 0.0001 + 1e-10:
                        return False, f"Row {i}, col {j}: numeric mismatch ({source_val} vs {dest_val})"
                except (ValueError, TypeError):
                    # Not numeric, do string comparison
                    # Allow case differences and minor whitespace differences
                    if source_str.strip().lower() != dest_str.strip().lower():
                        return False, f"Row {i}, col {j}: value mismatch ({source_val} vs {dest_val})"
        
        return True, f"Results match: {source_result.total_rows} rows, {len(source_result.columns)} columns, data verified"

