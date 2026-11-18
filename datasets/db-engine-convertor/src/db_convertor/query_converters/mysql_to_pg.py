"""MySQL to PostgreSQL SQL query converter with tie-aware result comparison.

This module contains:
  - `MySQLToPGQueryConverter`: Builds the prompt for converting MySQL queries to
    PostgreSQL and implements `compare_results` to compare two result tables
    across 100% of columns:
      1. Check 1: Exact row-by-row match in the returned order (whole numbers
         and text exactly; other numbers to one part in a billion, one part in
         a million for MySQL floating-point values, or equal once the
         PostgreSQL value is rounded the way MySQL rounded its value).
      2. Check 2: Same exact rows across all columns, where rows may only swap
         places with rows they are tied with on the source query's top-level
         ORDER BY keys (so the sort order itself is always checked, and zero
         columns are ever ignored or dropped).
  - Helpers that read the source query's top-level ORDER BY keys and SELECT
    list (`_mask_sql`, `_split_top_level`, `_top_level_order_keys`,
    `_select_items`) so Check 2 knows which output columns are sort keys.
"""

import decimal
import math
import re
from typing import Any, Dict, List, Optional, Sequence, Tuple
from .base import QueryConverter, QueryResult


# A number written the way a database prints it: no leading zeros (so text
# codes like '007' or '02139' are never treated as numbers).
_CANONICAL_NUMBER_RE = re.compile(
    r"^-?(0|[1-9]\d*)(\.\d+)?([eE][-+]?\d+)?$"
)
# Relative tolerance for non-whole numbers: they must agree to one part in a
# billion (with the same 1e-9 as an absolute floor near zero), matching the
# existing converters' 1e-9 while staying fair to very large values.
_REL_TOL = 1e-9
_ABS_TOL = 1e-9
# Values MySQL returns as floating point come from its FLOAT columns (or math
# on them). MySQL FLOAT keeps only about 7 significant digits, while the same
# column may be stored at full precision in PostgreSQL, so these values may
# differ by one part in a million. Exact decimals, counts and IDs never get
# this leeway.
_MYSQL_FLOAT_REL_TOL = 1e-6
# A canonical whole number (no decimal point and no exponent).
_CANONICAL_INT_RE = re.compile(r"^-?(0|[1-9]\d*)$")
# Words that end an ORDER BY clause at the top level of a query.
_ORDER_END_RE = re.compile(
    r"\b(limit|offset|fetch|for\s+update|for\s+share|lock\s+in)\b|;",
    re.IGNORECASE,
)
# Sort modifiers that can follow an ORDER BY key. The COLLATE name is kept
# (group 2) so the caller can tell a case-sensitive sort from the default.
_ORDER_SUFFIX_RE = re.compile(
    r"(\s+collate\s+(\S+))?(\s+(asc|desc))?(\s+nulls\s+(first|last))?\s*$",
    re.IGNORECASE,
)
# A key sorted byte by byte in MySQL (`BINARY col`, `CAST(col AS BINARY)`).
_BINARY_KEY_RE = re.compile(r"^binary\b|\bas\s+binary\b", re.IGNORECASE)
# A plain (optionally table-qualified) column reference such as `t`.`col`.
_SIMPLE_REF_RE = re.compile(r"^[\w$]+(\.[\w$]+)*$")
# Pieces of a date/number format string (`'YYYY-MM-DD'`, `'FM999,990.00'`,
# `'HH24:MI'`, `'Dy'`); a literal made only of these is not data.
_FORMAT_TOKEN_RE = re.compile(
    r"(?:yyyy|iyyy|yy|mm|dd|hh24|hh12|hh|mi|ss|ms|us|fm|am|pm|a\.m\.|p\.m\.|"
    r"dy|day|mon|month|tz|iw|q|j|w|d|th|0|9|[\s,.:/\-_T\"'()+%])",
    re.IGNORECASE,
)
# Literals the model may add without copying data: interval units, sort
# sentinels for ROLLUP labels, collation names, booleans and special floats.
_HARMLESS_LITERALS = frozenset({
    "second", "seconds", "minute", "minutes", "hour", "hours", "day", "days",
    "week", "weeks", "month", "months", "quarter", "quarters", "year", "years",
    "epoch", "dow", "doy", "isodow", "century", "decade", "millisecond",
    "microsecond", "zzz", "total", "subtotal", "grand total", "und-x-icu",
    "c", "posix", "default", "utc", "true", "false", "t", "f", "nan",
    "infinity", "-infinity", "escape", "both", "leading", "trailing",
})


# `WHEN col = 48213` or `WHEN col IN (48213, ...` : a column compared with a
# number inside a CASE. Group 1 is the first number.
_WHEN_NUMBER_RE = re.compile(
    r"\bwhen\s+(?:[\w$\"]+\.)*[\w$\"]+\s*(?:=|in\s*\()\s*(-?\d+(?:\.\d+)?)",
    re.IGNORECASE,
)
# Clause keywords: scanning backwards from a CASE, whichever comes first tells
# which clause the CASE belongs to.
_CLAUSE_START_RE = re.compile(
    r"(order\s+by|select|from|where|group\s+by|having|on|set|limit|values)\b",
    re.IGNORECASE,
)


def _blank_string_literals(sql: str) -> str:
    """Returns `sql` with the inside of every '...' literal replaced by spaces.

    Unlike `_mask_sql`, double-quoted identifiers such as `"ctid"` are kept,
    and the result has the same length as `sql`.
    """
    return re.sub(
        r"'((?:[^']|'')*)'", lambda m: "'" + " " * len(m.group(1)) + "'", sql
    )


def _inside_order_by(text: str, pos: int) -> bool:
    """True if position `pos` of `text` sits inside an ORDER BY clause.

    Walks backwards from `pos`, skipping over complete parenthesised groups,
    until it meets a clause keyword at the same or an enclosing level. So a
    CASE directly in `ORDER BY ...`, in `OVER (... ORDER BY ...)` or wrapped
    in a function inside either one counts; a CASE in a SELECT list, WHERE or
    JOIN condition does not.

    Example:
        `"SELECT a FROM t ORDER BY b, CASE WHEN id = 7 THEN 0 END"` at the
        `WHEN` -> True; `"SELECT CASE WHEN id = 7 THEN 0 END FROM t"` -> False.

    Args:
        text: SQL text with string literals already blanked.
        pos: Character position to classify.

    Returns:
        Whether the nearest clause keyword before `pos` is ORDER BY.
    """
    depth = 0
    i = pos - 1
    while i >= 0:
        ch = text[i]
        if ch == ")":
            depth += 1
        elif ch == "(":
            # Leaving the group that holds `pos`: keep looking one level out.
            depth = max(depth - 1, 0)
        elif depth == 0 and (i == 0 or not (text[i - 1].isalnum() or text[i - 1] in "_$")):
            m = _CLAUSE_START_RE.match(text, i)
            if m:
                return m.group(1).lower().startswith("order")
        i -= 1
    return False


def _mask_sql(sql: str, mask_parens: bool) -> str:
    """Returns `sql` with quoted text and comments replaced by spaces.

    The result has the same length as `sql`, so positions found in it can be
    used to slice `sql`. Handles '...' (with '' and backslash escapes), "...",
    `...`, `--` and `#` line comments, and /* */ comments.

    Args:
        sql: A SQL query.
        mask_parens: If True, text inside parentheses is also replaced by
            spaces (the parentheses themselves too), leaving only the
            top level of the query visible.

    Returns:
        The masked query.
    """
    out = []
    i, n, depth = 0, len(sql), 0
    while i < n:
        ch = sql[i]
        # Line comments: blank out to the end of the line.
        if sql.startswith("--", i) or ch == "#":
            j = sql.find("\n", i)
            j = n if j < 0 else j
            out.append(" " * (j - i))
            i = j
            continue
        # Block comments: blank out to the closing */.
        if sql.startswith("/*", i):
            j = sql.find("*/", i + 2)
            j = n if j < 0 else j + 2
            out.append(" " * (j - i))
            i = j
            continue
        # Quoted text or identifiers: keep the quotes, blank the inside.
        if ch in "'\"`":
            j = i + 1
            while j < n:
                if sql[j] == "\\" and ch == "'":
                    j += 2
                    continue
                if sql[j] == ch:
                    if j + 1 < n and sql[j + 1] == ch:
                        j += 2
                        continue
                    break
                j += 1
            j = min(j, n - 1)
            inner = " " * (j - i - 1)
            if mask_parens and depth > 0:
                out.append(" " * (j - i + 1))
            else:
                out.append(ch + inner + sql[j] if j > i else ch)
            i = j + 1
            continue
        if mask_parens and ch == "(":
            depth += 1
            out.append(" ")
        elif mask_parens and ch == ")":
            depth = max(depth - 1, 0)
            out.append(" ")
        else:
            out.append(" " if (mask_parens and depth > 0) else ch)
        i += 1
    return "".join(out)


def _split_top_level(text: str) -> List[str]:
    """Splits `text` on commas that are not inside parentheses or quotes."""
    masked = _mask_sql(text, mask_parens=True)
    parts, start = [], 0
    for i, ch in enumerate(masked):
        if ch == ",":
            parts.append(text[start:i])
            start = i + 1
    parts.append(text[start:])
    return [p.strip() for p in parts if p.strip()]


def _top_level_order_items(sql: str) -> Optional[List[Tuple[str, bool]]]:
    """Returns `(key, case_insensitive)` for each key of the outermost ORDER BY.

    ORDER BY clauses inside parentheses (subqueries, CTEs, window functions)
    are ignored; only the one that sorts the final result counts. Each key is
    returned without its ASC/DESC, NULLS FIRST/LAST or COLLATE suffix.
    `case_insensitive` is True for MySQL's default text comparison and False
    when the key asks for a byte or case-sensitive sort (`COLLATE utf8mb4_bin`,
    `COLLATE ..._cs`, `BINARY col`, `CAST(col AS BINARY)`).

    Args:
        sql: A SQL query.

    Returns:
        The list of `(key expression, case_insensitive)`, or None if the final
        result is not sorted.
    """
    masked = _mask_sql(sql, mask_parens=True)
    matches = list(re.finditer(r"\border\s+by\b", masked, re.IGNORECASE))
    if not matches:
        return None
    start = matches[-1].end()
    end_match = _ORDER_END_RE.search(masked, start)
    end = end_match.start() if end_match else len(sql)
    items = []
    for key in _split_top_level(sql[start:end]):
        suffix = _ORDER_SUFFIX_RE.search(key)
        collation = (suffix.group(2) or "").strip("`\"'").lower() if suffix else ""
        key = _ORDER_SUFFIX_RE.sub("", key).strip()
        if key:
            case_insensitive = not _BINARY_KEY_RE.search(key) and (
                not collation or collation.endswith("_ci")
            )
            items.append((key, case_insensitive))
    return items or None


def _top_level_order_keys(sql: str) -> Optional[List[str]]:
    """Returns the sort keys of the query's outermost ORDER BY, or None.

    Same as `_top_level_order_items` without the case-sensitivity flags.
    """
    items = _top_level_order_items(sql)
    return [key for key, _ in items] if items else None


def _normalize_expr(text: str) -> str:
    """Lower-cases `text`, drops quote characters and extra spaces."""
    text = text.replace("`", "").replace('"', "").lower()
    text = re.sub(r"\s+", " ", text).strip()
    return re.sub(r"\s*([(),.])\s*", r"\1", text)


def _select_items(sql: str) -> List[Tuple[str, Optional[str]]]:
    """Returns `(expression, alias)` for each item of the outermost SELECT.

    For a UNION, this is the first branch, which names the output columns.

    Args:
        sql: A SQL query.

    Returns:
        A list of `(normalized expression, normalized alias or None)`.
    """
    masked = _mask_sql(sql, mask_parens=True)
    sel = re.search(r"\bselect\b(\s+(distinct|all)\b)?", masked, re.IGNORECASE)
    if not sel:
        return []
    frm = re.search(
        r"\b(from|union|intersect|except|order\s+by|limit)\b",
        masked[sel.end():],
        re.IGNORECASE,
    )
    end = sel.end() + frm.start() if frm else len(sql)
    items = []
    for item in _split_top_level(sql[sel.end():end]):
        m = re.match(
            r"^(?P<expr>.*?)\s+(as\s+)?(?P<alias>[`\"]?[\w$ ]+[`\"]?)$",
            item,
            re.IGNORECASE | re.DOTALL,
        )
        expr, alias = item, None
        if m and not re.search(r"[-+*/|=<>,(]\s*$", m.group("expr")):
            alias_text = m.group("alias").strip()
            # A bare trailing word is only an alias if it is quoted or the
            # expression before it is complete (ends in a name, quote or ")").
            if re.search(r"\bas\s+\S+$", item, re.IGNORECASE) or re.search(
                r"[\w`\")]$", m.group("expr").strip()
            ):
                expr, alias = m.group("expr"), _normalize_expr(alias_text)
        items.append((_normalize_expr(expr), alias))
    return items


class MySQLToPGQueryConverter(QueryConverter):
    """Convert SQL queries from MySQL to PostgreSQL."""

    def __init__(self, allow_tie_equivalence: bool = True):
        """Initialize the MySQL to PostgreSQL query converter.

        Args:
            allow_tie_equivalence: Whether to treat two queries as matching when
                they return the exact same rows in a different tie order, or
                when they match across 100% of columns after breaking ties the
                same way in both queries. Defaults to True.
        """
        super().__init__("mysql", "postgresql")
        self.allow_tie_equivalence = allow_tie_equivalence

    @staticmethod
    def reject_candidate(
        source_query: str, converted_query: str, schema: str = ""
    ) -> str:
        """Returns why a candidate query is disallowed, or "" if it is fine.

        Three tricks can make a PostgreSQL query reproduce MySQL's accidental
        order of tied rows without being a real translation, so they are
        refused outright:
          1. Sorting by `ctid` (the physical row position), written bare or
             as `"ctid"`.
          2. Copying data values into the query (`CASE WHEN title =
             'Statistical Thermodynamics' THEN 1 ...`): any quoted text in the
             candidate that does not appear in the MySQL query. Format
             strings (`'YYYY-MM-DD'`, `'FM999.00'`), regexes, numbers,
             interval units, collation names, ROLLUP label sentinels, one- or
             two-character literals, and values that appear in the table
             definitions (such as ENUM members) are allowed.
          3. Copying a data value as a number into a sort (`ORDER BY sal DESC,
             CASE WHEN id = 48213 THEN 0 ELSE 1 END`): a CASE that compares a
             column with a number, inside an ORDER BY (including a window's
             ORDER BY), where that number is not in the MySQL query. `0` and
             `1` are allowed (flags), and numbers elsewhere in the query
             (`ROUND(x, 4)`, `LIMIT 10`, casts) are never looked at.

        Example:
            `reject_candidate("SELECT a FROM t ORDER BY a", "SELECT a FROM t ORDER BY a, ctid")`
            -> `"uses ctid (physical row order) to copy MySQL's tie order"`

        Args:
            source_query: The MySQL query.
            converted_query: The candidate PostgreSQL query.
            schema: Optional table definitions; quoted values found there
                (ENUM members, defaults) are not treated as copied data.

        Returns:
            A short reason, or an empty string.
        """
        if not converted_query:
            return ""
        schema_lower = (schema or "").lower()
        masked = _mask_sql(converted_query, mask_parens=False)
        # `_mask_sql` blanks quoted identifiers, so `"ctid"` is looked for in
        # a copy where only the '...' string literals are blanked.
        no_strings = _blank_string_literals(converted_query)
        if re.search(r"\bctid\b", masked, re.IGNORECASE) or re.search(
            r'"ctid"', no_strings, re.IGNORECASE
        ):
            return "uses ctid (physical row order) to copy MySQL's tie order"
        src_lower = source_query.lower()
        src_literals = {s.lower() for s in re.findall(r"'((?:[^']|'')*)'", source_query)}
        bad = []
        for lit in re.findall(r"'((?:[^']|'')*)'", converted_query):
            low = lit.replace("''", "'").strip().lower()
            if (
                len(low) <= 2
                or low in src_literals
                or low in _HARMLESS_LITERALS
                or low.startswith(("^", "[", "%", "\\", "(?"))
                or re.search(r"\\[dswb]", low)
                or not _FORMAT_TOKEN_RE.sub("", low)
                or re.fullmatch(r"-?\d+(\.\d+)?(e[-+]?\d+)?", low)
                or re.fullmatch(r"\d+\s+[a-z]+", low)
                or re.search(r"(?<![\w])" + re.escape(low) + r"(?![\w])", src_lower)
                or (schema_lower and f"'{low}'" in schema_lower)
            ):
                continue
            if low not in bad:
                bad.append(low)
        if bad:
            shown = ", ".join(f"'{b}'" for b in bad[:3])
            return (
                f"hard-codes data value(s) {shown} that are not in the MySQL "
                "query (copying MySQL's tie order is not a conversion)"
            )
        # A column compared with a number in a CASE inside an ORDER BY, where
        # the MySQL query never mentions that number.
        bad_numbers = []
        for m in _WHEN_NUMBER_RE.finditer(no_strings):
            number = m.group(1)
            if number in ("0", "1") or re.search(
                r"(?<![\w.])" + re.escape(number) + r"(?![\w.])", source_query
            ):
                continue
            if _inside_order_by(no_strings, m.start()) and number not in bad_numbers:
                bad_numbers.append(number)
        if bad_numbers:
            return (
                f"hard-codes data value(s) {', '.join(bad_numbers[:3])} in an "
                "ORDER BY CASE that are not in the MySQL query (copying "
                "MySQL's tie order is not a conversion)"
            )
        return ""

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
        attempt_history: Optional[List[Dict]] = None,
    ) -> str:
        """Build the LLM prompt for converting a MySQL query to PostgreSQL.

        Args:
            source_schema: Text of the MySQL table definitions.
            dest_schema: Text of the PostgreSQL table definitions.
            source_query: Original MySQL query string.
            converted_query: Most recent candidate PostgreSQL query string.
            source_result: Result of running `source_query` on MySQL.
            dest_result: Result of running `converted_query` on PostgreSQL.
            attempt: Current 1-based attempt number.
            max_attempts: Maximum number of conversion attempts allowed.
            attempt_history: List of earlier attempts with their SQL, result, and notes.

        Returns:
            Prompt string sent to the LLM conversion agent.
        """
        prompt = f"""You are an expert SQL query converter (ReAct agent) specializing in MySQL to PostgreSQL migrations.

**YOUR GOAL:** Convert a MySQL query to PostgreSQL and ensure results are SEMANTICALLY EQUIVALENT.

**MATCHING CRITERIA (for result_matched):**
- Same number of rows
- Same data values (meaning-wise equivalent)
- Allow precision differences (e.g., 0.5 vs 0.500000)
- Allow type differences (e.g., INTEGER vs DOUBLE PRECISION) if values match

**CONVERSION RULES FOR POSTGRESQL:**

1. **Column and Table Names**: MUST use double quotes
   - MySQL: `Table`.`Column Name` → PostgreSQL: "Table"."Column Name"

2. **Type Casting**: Use PostgreSQL casting
   - MySQL: CAST(x AS REAL) → PostgreSQL: x::DOUBLE PRECISION

3. **NULL Handling**: MySQL puts NULLs FIRST on ASC and LAST on DESC; PostgreSQL does the opposite by default
   - MySQL: ORDER BY column ASC → PostgreSQL: ORDER BY column ASC NULLS FIRST
   - MySQL: ORDER BY column DESC → PostgreSQL: ORDER BY column DESC NULLS LAST

3b. **Text Sort Order**: MySQL's text order differs from PostgreSQL's default, which ignores spaces and
   punctuation (MySQL: 'Adv Comp' < 'Advanced', 'D-Lab' < 'Defects'; PostgreSQL default: the reverse).
   When sorting by a text column (in a final ORDER BY or inside OVER (... ORDER BY ...)), pick the
   PostgreSQL sort expression from the MySQL table's collation shown in the source schema:
   - `COLLATE=utf8mb4_0900_ai_ci` (or any `utf8mb4_0900_*`) or `*_unicode_ci`:
     MySQL: ORDER BY title → PostgreSQL: ORDER BY "title" COLLATE "und-x-icu" ASC NULLS FIRST
   - `*_general_ci`, or a table with only `DEFAULT CHARSET=utf8mb3` / `latin1` and no COLLATE
     (their default collations are `utf8mb3_general_ci` / `latin1_swedish_ci`):
     MySQL: ORDER BY title → PostgreSQL: ORDER BY UPPER("title") COLLATE "C" ASC NULLS FIRST
   - Only for text columns (COLLATE on a number column is an error). Do not use ctid or
     hand-written CASE overrides to imitate MySQL's order of tied rows; tied rows may come in any order.

4. **Division**: PostgreSQL integer division returns integer
   - MySQL: col1 / col2 (auto-converts to REAL)
   - PostgreSQL: col1::DOUBLE PRECISION / col2 (for real division)

5. **Boolean Values**:
   - MySQL: 0/1 → PostgreSQL: Use explicit comparison (= 1, = 0)

===== SCHEMAS =====

SOURCE (MySQL):
{source_schema}

DESTINATION (PostgreSQL):
{dest_schema}

===== SOURCE QUERY (MySQL) =====
{source_query}
"""

        # Include the source execution summary when available.
        if source_result:
            prompt += f"""
===== SOURCE EXECUTION RESULT =====
{source_result.get_summary()}
"""

        # Include the history of prior attempts so the agent does not repeat failed queries.
        if attempt_history:
            prompt += "\n===== PREVIOUS ATTEMPTS =====\n"
            for hist in attempt_history:
                prompt += f"\n--- Attempt {hist['attempt']} ---\n"
                prompt += f"Query:\n{hist['query']}\n\n"
                result = hist["result"]
                if result.error:
                    prompt += f"Result: ERROR - {result.error}\n"
                else:
                    prompt += f"Result: {result.total_rows} rows\n"
                prompt += f"Notes: {hist['notes']}\n"
            prompt += "\n"

        # Include the current candidate query and comparison result if present.
        if converted_query:
            prompt += f"""
===== CURRENT CONVERSION ATTEMPT =====
{converted_query}
"""
            veto = self.reject_candidate(
                source_query, converted_query, source_schema + "\n" + dest_schema
            )
            if veto:
                prompt += f"""
===== REJECTED =====
✗ This attempt is REJECTED regardless of its results: it {veto}.
Such a query can never be accepted. Write a plain translation of the MySQL query instead; if the
MySQL result depends on how tied rows happen to be ordered, declare "unable_to_match".
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
                        matches, reason = self.compare_results(
                            source_result, dest_result, source_query=source_query
                        )
                        if matches:
                            prompt += f"✓ Data MATCHES: {reason}\n"
                        else:
                            prompt += f"✗ Data MISMATCH: {reason}\n"
                    else:
                        prompt += "✗ Row counts MISMATCH\n"

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
        dest_result: QueryResult,
        source_query: Optional[str] = None,
    ) -> Tuple[bool, str]:
        """Compare MySQL and PostgreSQL query results across 100% of columns.

        Checks in order:
          1. Check 1 (exact row order): Compares row 1 to row 1, row 2 to row 2,
             and so on across every column (see `_values_equal`: whole numbers
             and text must match exactly; other numbers must agree to one part
             in a billion, or a million for MySQL floating-point values, or the
             MySQL value must be the PostgreSQL value rounded to the MySQL
             value's number of decimal places).
          2. Check 2 (same rows in a different tie order): If
             `allow_tie_equivalence` is True and `source_query` is given, rows
             may only swap places with rows that are tied on the source query's
             top-level ORDER BY keys (see `_order_respecting_match`). If the
             source query has no top-level ORDER BY, row order is undefined and
             any order is accepted. Zero columns are ever ignored or dropped.

        Args:
            source_result: Result from running the MySQL query.
            dest_result: Result from running the PostgreSQL query.
            source_query: The MySQL query that produced `source_result`. Needed
                for Check 2; without it, rows in a different order never match.

        Returns:
            Tuple of `(matches, reason)` indicating whether the results match.
        """
        # If either query failed with a database error, they cannot match.
        if source_result.error and dest_result.error:
            return (
                False,
                f"Both queries failed. Source: {source_result.error}, Dest: {dest_result.error}",
            )
        if source_result.error:
            return False, f"Source query error: {source_result.error}"
        if dest_result.error:
            return False, f"Destination query error: {dest_result.error}"

        # Both tables must have the exact same number of rows and columns.
        if source_result.total_rows != dest_result.total_rows:
            return (
                False,
                f"Row count mismatch: source={source_result.total_rows}, dest={dest_result.total_rows}",
            )

        if len(source_result.columns) != len(dest_result.columns):
            return (
                False,
                f"Column count mismatch: source={len(source_result.columns)}, dest={len(dest_result.columns)}",
            )

        if len(source_result.rows) != len(dest_result.rows):
            return (
                False,
                f"Data row mismatch: source={len(source_result.rows)}, dest={len(dest_result.rows)}",
            )

        if source_result.total_rows == 0:
            return True, "All checks passed: both queries returned 0 rows"

        # Check 1: Compare every cell row-by-row in the exact returned order.
        first_mismatch_reason = None
        for i, (src_row, dest_row) in enumerate(
            zip(source_result.rows, dest_result.rows)
        ):
            if len(src_row) != len(dest_row):
                return False, f"Row {i + 1} column count mismatch"
            for j, (src_val, dest_val) in enumerate(zip(src_row, dest_row)):
                if not self._values_equal(src_val, dest_val):
                    first_mismatch_reason = (
                        f"Row {i + 1}, column {j + 1} mismatch: "
                        f"source={src_val}, dest={dest_val}"
                    )
                    break
            if first_mismatch_reason is not None:
                break

        if first_mismatch_reason is None:
            return (
                True,
                "All checks passed: row counts match, column counts match, data matches",
            )

        # If tie-equivalence is turned off (--strict-row-order), stop here.
        if not self.allow_tie_equivalence:
            return False, first_mismatch_reason

        # Check 2 needs the source query to know which columns set the order.
        if source_query is None:
            return False, first_mismatch_reason

        ok, why = self._order_respecting_match(
            source_query, source_result, dest_result
        )
        if ok:
            return (
                True,
                "All checks passed (same rows reordered): exact same set of rows "
                f"across all columns ({why})",
            )
        return False, f"{first_mismatch_reason}; {why}"

    def _order_respecting_match(
        self,
        source_query: str,
        source_result: QueryResult,
        dest_result: QueryResult,
    ) -> Tuple[bool, str]:
        """Check 2: same rows, where rows may only move within ORDER BY ties.

        If the source query has no top-level ORDER BY, the database may return
        rows in any order, so the two results only need the same rows. If it
        has one, each ORDER BY key is matched to an output column, both results
        are cut into consecutive groups of rows that share the same key values
        (using MySQL's notion of "the same": case-insensitive text, trailing
        spaces ignored), and the groups must appear in the same order with the
        same rows inside. So a reversed sort, different NULL placement or a
        different text sort order is rejected, while a different order among
        tied rows is accepted.

        Args:
            source_query: The MySQL query that produced `source_result`.
            source_result: Result from running the MySQL query.
            dest_result: Result from running the PostgreSQL query.

        Returns:
            `(matches, explanation)`.
        """
        all_cols = list(range(len(source_result.columns)))
        order_items = _top_level_order_items(source_query)
        # No ORDER BY: only the set of rows matters.
        if order_items is None:
            if self._rows_match_on_columns(
                source_result.rows, dest_result.rows, all_cols
            ):
                return True, "the source query has no ORDER BY, so row order is free"
            return False, "the rows differ, not just their order"

        keys = [key for key, _ in order_items]
        case_flags = [ci for _, ci in order_items]
        key_cols = self._order_key_columns(keys, source_query, source_result)
        if key_cols is None:
            return (
                False,
                "row order differs and the ORDER BY keys are not all output "
                "columns, so the order cannot be checked",
            )

        src_groups = self._tie_groups(
            source_result.rows, key_cols, case_flags, dest_result.rows
        )
        dest_groups = self._tie_groups(
            dest_result.rows, key_cols, case_flags, source_result.rows
        )
        if len(src_groups) != len(dest_groups):
            return False, "the rows are not in the same ORDER BY order"
        for (src_key, src_rows), (dest_key, dest_rows) in zip(
            src_groups, dest_groups
        ):
            same_key = all(
                self._keys_tied(a, b, src_vs_dest=True, case_insensitive=ci)
                for a, b, ci in zip(src_key, dest_key, case_flags)
            )
            if (
                not same_key
                or len(src_rows) != len(dest_rows)
                or not self._rows_match_on_columns(src_rows, dest_rows, all_cols)
            ):
                return False, "the rows are not in the same ORDER BY order"
        return True, "only rows tied on the ORDER BY keys changed places"

    @staticmethod
    def _order_key_columns(
        keys: Sequence[str], source_query: str, source_result: QueryResult
    ) -> Optional[List[int]]:
        """Maps each ORDER BY key to the output column it sorts by.

        A key matches a column by position (`ORDER BY 2`), by a SELECT alias
        (MySQL resolves aliases first), by being the same expression as a
        SELECT item, or by output column name. A table-qualified key such as
        `o.order_date` only matches by name when the SELECT list has a `*`
        (otherwise it could be a hidden column that merely shares a name with
        a visible one).

        Args:
            keys: ORDER BY keys from `_top_level_order_keys`.
            source_query: The MySQL query.
            source_result: Its result (for the output column names).

        Returns:
            One column index per key, or None if any key is not an output
            column (then the order cannot be checked from the results).
        """
        names = [_normalize_expr(str(c)) for c in source_result.columns]
        items = _select_items(source_query)
        stars = [
            i
            for i, (expr, _) in enumerate(items)
            if expr == "*" or expr.endswith(".*")
        ]
        has_star = bool(stars)

        def _item_col_index(i: int, col_name: Optional[str]) -> Optional[int]:
            """Maps SELECT item `i` to its index in `source_result.columns`."""
            if not stars:
                return i if i < len(names) else None
            if len(names) >= len(items):
                if i < stars[0]:
                    return i
                if i > stars[-1]:
                    return len(names) - (len(items) - i)
            if col_name is not None:
                hits = [k for k, name in enumerate(names) if name == col_name]
                if len(hits) == 1:
                    return hits[0]
            return None

        out = []
        for key in keys:
            norm = _normalize_expr(key)
            idx = None
            if re.fullmatch(r"\d+", norm):
                n = int(norm) - 1
                idx = n if 0 <= n < len(names) else None
            # A SELECT alias (checked first, as MySQL does).
            if idx is None:
                for i, (_, alias) in enumerate(items):
                    if alias is not None and alias == norm:
                        idx = _item_col_index(i, alias)
                        if idx is not None:
                            break
            # Same expression as a SELECT item (e.g. `t.col` or `SUM(x)`).
            if idx is None:
                for i, (expr, alias) in enumerate(items):
                    if expr == norm:
                        idx = _item_col_index(i, alias or expr)
                        if idx is not None:
                            break
            # Output column name (for `t.col`, the name is `col`, but only
            # when a `*` in the SELECT list can have produced that column).
            if idx is None:
                qualified = _SIMPLE_REF_RE.match(norm) and "." in norm
                if not qualified or has_star:
                    short = norm.split(".")[-1] if _SIMPLE_REF_RE.match(norm) else norm
                    for i, name in enumerate(names):
                        if name in (norm, short):
                            idx = i
                            break
            if idx is None:
                return None
            out.append(idx)
        return out

    def _tie_groups(
        self,
        rows: Sequence[Tuple],
        key_cols: Sequence[int],
        case_flags: Optional[Sequence[bool]] = None,
        other_rows: Optional[Sequence[Tuple]] = None,
    ) -> List[Tuple[Tuple, List[Tuple]]]:
        """Cuts `rows` into consecutive groups that share the same key values.

        Rounding error in the first sort key: a computed key (such as a
        standard deviation) can come out as `1.4142135623730951` for one row
        and `1.4142135623730950` for the next, even though the true values are
        equal. The database then orders those rows by that last-digit
        difference and never looks at the later sort keys, and the other
        database may round the other way. So when a run of rows agrees on the
        first key to one part in a billion but ONE result holds two different
        values there, the run is kept as one tie group (later keys are not
        used to split it).

        The two results are never compared with each other for this: MySQL
        printing `11.3333` where PostgreSQL prints `11.3333333333333333` is
        not rounding error inside a result, so rows that are exactly tied on
        the first key in both results are still split by the later keys.

        Args:
            rows: Rows of one result, in returned order.
            key_cols: Output column index of each ORDER BY key.
            case_flags: Per key, whether text compares case-insensitively.
            other_rows: Rows of the other result, so both results are cut the
                same way when only one of them has the rounding error.

        Returns:
            A list of `(key values, rows)` groups in order.
        """
        flags = list(case_flags) if case_flags else [True] * len(key_cols)
        if not rows:
            return []
        multi_key = len(key_cols) > 1
        clusters = (
            self._first_key_clusters(rows, key_cols[0], flags[0])
            if multi_key
            else [list(rows)]
        )
        other_clusters: List[List[Tuple]] = []
        if multi_key and other_rows and len(other_rows) == len(rows):
            other_clusters = self._first_key_clusters(
                other_rows, key_cols[0], flags[0]
            )
        aligned = len(other_clusters) == len(clusters)
        groups: List[Tuple[Tuple, List[Tuple]]] = []
        for idx, cluster in enumerate(clusters):
            if multi_key and (
                self._has_rounding_error(cluster, key_cols[0])
                or (
                    aligned
                    and self._has_rounding_error(other_clusters[idx], key_cols[0])
                )
            ):
                key = (cluster[0][key_cols[0]],) + (None,) * (len(key_cols) - 1)
                groups.append((key, list(cluster)))
                continue
            # Start a fresh group at each cluster so a row is never added to
            # a rounding-error group that came before it.
            start = len(groups)
            for row in cluster:
                key = tuple(row[c] for c in key_cols)
                if len(groups) > start and all(
                    self._keys_tied(a, b, case_insensitive=ci)
                    for a, b, ci in zip(groups[-1][0], key, flags)
                ):
                    groups[-1][1].append(row)
                else:
                    groups.append((key, [row]))
        return groups

    def _first_key_clusters(
        self, rows: Sequence[Tuple], col: int, case_insensitive: bool
    ) -> List[List[Tuple]]:
        """Cuts `rows` into consecutive runs that are tied on column `col`."""
        clusters: List[List[Tuple]] = []
        for row in rows:
            if clusters and self._keys_tied(
                clusters[-1][0][col], row[col], case_insensitive=case_insensitive
            ):
                clusters[-1].append(row)
            else:
                clusters.append([row])
        return clusters

    def _has_rounding_error(self, cluster: Sequence[Tuple], col: int) -> bool:
        """True if rows of ONE result, tied on `col`, hold two different values.

        Only non-whole numbers count (`1.4142135623730951` next to
        `1.4142135623730950`); whole numbers and text are tied only when equal,
        so they never differ inside a cluster.
        """
        values = {
            row[col]
            for row in cluster
            if isinstance(row[col], (float, decimal.Decimal))
            and not self._is_whole(row[col])
        }
        return len(values) > 1

    @classmethod
    def _keys_tied(
        cls,
        val1: Any,
        val2: Any,
        src_vs_dest: bool = False,
        case_insensitive: bool = True,
    ) -> bool:
        """True if MySQL would treat two sort-key values as tied.

        Within one result (`src_vs_dest=False`) two keys are tied only when
        they are the same value: whole numbers exactly (`1` and `1.0` tie),
        exact decimals once both are rounded to the fewer of their decimal
        places or 9 places (so money amounts `98765432.10` and `98765432.11`
        do not tie, while two computed averages that only differ in the 15th
        decimal do), floating-point values to one part in a billion, and text
        byte for byte, except that MySQL's default `_ci` collations ignore
        letter case, so text is lower-cased first when `case_insensitive` is
        True. When comparing a MySQL key (`val1`) with a PostgreSQL key
        (`val2`, `src_vs_dest=True`), numbers follow the rules of
        `_numbers_match`.
        """
        if val1 is None or val2 is None:
            return val1 is None and val2 is None
        if not (isinstance(val1, str) and isinstance(val2, str)):
            num1, num2 = cls._as_number(val1), cls._as_number(val2)
            if num1 is not None and num2 is not None:
                if src_vs_dest:
                    return cls._numbers_match(num1, num2, val1, _ABS_TOL)
                return cls._same_number(num1, num2)
        s1, s2 = str(val1), str(val2)
        if case_insensitive:
            s1, s2 = s1.lower(), s2.lower()
        return s1 == s2

    @classmethod
    def _same_number(cls, num1: Any, num2: Any) -> bool:
        """True if two numbers from the SAME result are the same sort key.

        Whole numbers must be equal exactly, and a whole number never ties
        with a non-whole one. If either is a float, they must agree to one
        part in a billion. Two exact decimals are both rounded to 9 decimal
        places and must then be equal, so stored amounts (`98765432.10` vs
        `98765432.11`, `0.5` vs `0.49`) compare exactly while long computed
        decimals that only differ far after the ninth place tie.
        """
        whole1, whole2 = cls._is_whole(num1), cls._is_whole(num2)
        if whole1 and whole2:
            return int(num1) == int(num2)
        if isinstance(num1, float) or isinstance(num2, float):
            f1, f2 = float(num1), float(num2)
            if math.isnan(f1) or math.isnan(f2):
                return math.isnan(f1) and math.isnan(f2)
            return math.isclose(f1, f2, rel_tol=_REL_TOL, abs_tol=_ABS_TOL)
        try:
            d1, d2 = decimal.Decimal(str(num1)), decimal.Decimal(str(num2))
            if whole1 or whole2:
                # A whole number only ties with the exact same value (`1` and
                # `1.0`), never with `0.498...` rounded down.
                return d1 == d2
            unit = decimal.Decimal(1).scaleb(-9)
            return d1.quantize(unit, rounding=decimal.ROUND_HALF_UP) == d2.quantize(
                unit, rounding=decimal.ROUND_HALF_UP
            )
        except decimal.InvalidOperation:
            return False

    @staticmethod
    def _as_number(val: Any) -> Optional[Any]:
        """Returns `val` as an int or Decimal/float if it is a number, else None.

        Booleans count as 0/1 (MySQL has no boolean type). Text counts as a
        number only if it is written canonically, with no leading zeros, so
        codes like '007' stay text.
        """
        if isinstance(val, bool):
            return int(val)
        if isinstance(val, (int, float, decimal.Decimal)):
            return val
        if isinstance(val, str) and _CANONICAL_NUMBER_RE.match(val):
            if _CANONICAL_INT_RE.match(val):
                return int(val)
            return decimal.Decimal(val)
        return None

    @staticmethod
    def _is_whole(num: Any) -> bool:
        """True for whole-number values: ints, Decimals with no non-zero
        fractional digits (`5`, `Decimal('5.00')`), and floats with no
        fractional part (`1234567.0`)."""
        if isinstance(num, int):
            return True
        if isinstance(num, decimal.Decimal) and num.is_finite():
            return num == num.to_integral_value()
        if isinstance(num, float) and math.isfinite(num):
            return num.is_integer()
        return False

    @staticmethod
    def _decimal_places(val: Any) -> int:
        """Number of digits after the decimal point MySQL printed for `val`.

        Only exact decimals carry this (MySQL `DECIMAL` results, including
        `AVG` and `/`, come back as `Decimal`, e.g. `Decimal('11.3333')` has 4).
        Floats and whole numbers return 0.
        """
        if isinstance(val, str) and _CANONICAL_NUMBER_RE.match(val):
            val = decimal.Decimal(val)
        if isinstance(val, decimal.Decimal) and val.is_finite():
            return max(-val.as_tuple().exponent, 0)
        return 0

    @classmethod
    def _numbers_match(
        cls, src_num: Any, dest_num: Any, src_raw: Any, epsilon: float
    ) -> bool:
        """Compares a MySQL number with a PostgreSQL number.

        Rules:
          - Both whole numbers (including floats such as `1234567.0`): must be
            exactly equal (compared as integers).
          - A whole MySQL integer or decimal against a non-whole PostgreSQL
            value never matches (`1000000000` vs `1000000000.5`). A whole
            MySQL *float* against a non-whole value falls through to the
            tolerance below, since float math can land on a whole number by
            chance on one side only.
          - Otherwise they match if they agree to one part in a billion
            (`_REL_TOL`, with `epsilon` as the absolute floor near zero), or
            to one part in a million (`_MYSQL_FLOAT_REL_TOL`) when MySQL
            returned the value as floating point (its 7-digit FLOAT columns).
          - Or if the MySQL value is a decimal with N digits after the point
            and it equals the PostgreSQL value rounded (half away from zero)
            to N digits. That covers MySQL showing `AVG` and division to 4
            decimals (11.3333 vs 11.333333...) without allowing any other
            difference: 11.3333 vs 11.3340 still fails, and a whole MySQL
            number (N = 0) is never matched by rounding.

        Args:
            src_num: MySQL value as a number (from `_as_number`).
            dest_num: PostgreSQL value as a number (from `_as_number`).
            src_raw: The original MySQL value (to read its type and decimal
                places).
            epsilon: Absolute tolerance near zero.

        Returns:
            True if the two numbers match.
        """
        src_whole, dest_whole = cls._is_whole(src_num), cls._is_whole(dest_num)
        if src_whole and dest_whole:
            return int(src_num) == int(dest_num)
        if (
            src_whole
            and not isinstance(src_num, float)
            and cls._decimal_places(src_raw) == 0
        ):
            return False
        both_exact = not isinstance(src_num, float) and not isinstance(
            dest_num, float
        )
        if both_exact:
            # Two exact decimals (MySQL DECIMAL vs PostgreSQL numeric) never
            # get a tolerance: the only allowed difference is PostgreSQL
            # carrying more decimal places than MySQL printed.
            return cls._rounds_to(src_num, dest_num, src_raw)
        f1, f2 = float(src_num), float(dest_num)
        if math.isnan(f1) or math.isnan(f2):
            return math.isnan(f1) and math.isnan(f2)
        rel = (
            _MYSQL_FLOAT_REL_TOL
            if isinstance(src_raw, float)
            else _REL_TOL
        )
        if math.isclose(f1, f2, rel_tol=rel, abs_tol=epsilon):
            return True
        return cls._rounds_to(src_num, dest_num, src_raw)

    @classmethod
    def _rounds_to(cls, src_num: Any, dest_num: Any, src_raw: Any) -> bool:
        """True if the PostgreSQL value rounds to the MySQL value's decimals.

        MySQL prints `AVG` and `/` results to 4 decimal places (e.g.
        `11.3333`); PostgreSQL keeps more (`11.333333...`). The two match when
        the PostgreSQL value, rounded half away from zero to the number of
        decimals MySQL printed, equals the MySQL value. A whole MySQL number
        (no decimals) never matches this way.

        Args:
            src_num: MySQL value as a number.
            dest_num: PostgreSQL value as a number.
            src_raw: The original MySQL value (to count its decimal places).

        Returns:
            True if the rounded PostgreSQL value equals the MySQL value.
        """
        places = cls._decimal_places(src_raw)
        if places == 0:
            return False
        try:
            src_dec = decimal.Decimal(str(src_num))
            dest_dec = decimal.Decimal(str(dest_num)).quantize(
                decimal.Decimal(1).scaleb(-places),
                rounding=decimal.ROUND_HALF_UP,
            )
        except (decimal.InvalidOperation, ValueError):
            return False
        return src_dec == dest_dec

    def _values_equal(
        self,
        val1: Any,
        val2: Any,
        epsilon: float = 1e-9,
    ) -> bool:
        """Check whether a MySQL cell value matches a PostgreSQL cell value.

        Rules:
          - Two NULLs match; a NULL and a value do not.
          - Two text values must match exactly, even when they look like
            numbers (`'6.1'` is not `'6.14'`).
          - Numbers follow `_numbers_match`: whole numbers (counts, sums of
            integers, IDs) exactly; others to one part in a billion (one part
            in a million for MySQL floating-point values), or equal after
            rounding the PostgreSQL value the way MySQL rounded its own.
          - Everything else must print identically.

        Args:
            val1: Cell value from the MySQL result (order matters: only the
                MySQL value may be the rounded one).
            val2: Cell value from the PostgreSQL result.
            epsilon: Absolute tolerance for non-whole numbers near zero.

        Returns:
            True if the two values match.
        """
        if val1 is None or val2 is None:
            return val1 is None and val2 is None
        if val1 == val2 and type(val1) is type(val2):
            return True
        if isinstance(val1, str) and isinstance(val2, str):
            return False

        num1, num2 = self._as_number(val1), self._as_number(val2)
        if num1 is not None and num2 is not None:
            return self._numbers_match(num1, num2, val1, epsilon)

        # Everything else (text, dates, times) must print identically.
        return str(val1) == str(val2)

    @classmethod
    def _canonicalize_value(
        cls, val: Any, round_digits: Optional[int] = 3
    ) -> Tuple:
        """Turn a cell value into a sortable `(type_tag, ...)` tuple.

        Only used to line rows up before `_values_equal` compares them, so it
        just needs to put equal values next to each other.

        Example:
            - `_canonicalize_value(None)`      -> `("N", 0.0, 0)`
            - `_canonicalize_value("11.3333")` -> `("F", 11.333, 0)`
            - `_canonicalize_value("Biology")` -> `("S", "Biology")`

        Args:
            val: Raw cell value from a database query result.
            round_digits: Number of decimal places to round numbers to when
                sorting rows, so numbers differing only far after the decimal
                point sort into the same place.

        Returns:
            A tuple that Python can sort even when a column mixes NULLs,
            numbers and text.
        """
        if val is None:
            return ("N", 0.0, 0)
        num = cls._as_number(val)
        if num is not None:
            f = float(num)
            if math.isnan(f):
                return ("N", 1.0, 0)
            # Whole numbers also keep their exact value, so two large IDs that
            # round to the same float still sort apart.
            exact = int(num) if cls._is_whole(num) else 0
            rounded = f if round_digits is None else round(f, round_digits)
            return ("F", rounded, exact)
        return ("S", str(val))

    def _rows_match_on_columns(
        self,
        src_rows: Sequence[Tuple],
        dest_rows: Sequence[Tuple],
        cols: Sequence[int],
    ) -> bool:
        """Check whether sorting both tables on `cols` produces identical rows.

        Args:
            src_rows: Rows from the MySQL result.
            dest_rows: Rows from the PostgreSQL result.
            cols: Column indices to sort and compare (all columns in the table).

        Returns:
            True if every row in `src_rows` matches `dest_rows` across `cols` after sorting.
        """
        if not cols or len(src_rows) != len(dest_rows):
            return False

        # Try rounding sort keys to 8, 6, 4, 3, and 2 decimal places when sorting
        # so small numbers like 0.018766 and 0.019169 sort apart while numbers
        # differing only in the last digit still line up, then verify every cell
        # pair using `_values_equal`.
        for digits in (8, 6, 4, 3, 2):
            src_sorted = [
                tuple(r[c] for c in cols)
                for _, r in sorted(
                    zip(
                        [
                            tuple(
                                self._canonicalize_value(
                                    r[c], round_digits=digits
                                )
                                for c in cols
                            )
                            for r in src_rows
                        ],
                        src_rows,
                    ),
                    key=lambda item: item[0],
                )
            ]
            dest_sorted = [
                tuple(r[c] for c in cols)
                for _, r in sorted(
                    zip(
                        [
                            tuple(
                                self._canonicalize_value(
                                    r[c], round_digits=digits
                                )
                                for c in cols
                            )
                            for r in dest_rows
                        ],
                        dest_rows,
                    ),
                    key=lambda item: item[0],
                )
            ]
            if all(
                all(self._values_equal(a, b) for a, b in zip(src_r, dest_r))
                for src_r, dest_r in zip(src_sorted, dest_sorted)
            ):
                return True
        return False
