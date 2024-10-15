from scorers import comparator
from typing import Tuple
import re


class ErrorCategorizer(comparator.Comparator):
    """ErrorCategorizer scorer categorizes the errors in the generated SQL query."""

    def __init__(self):
        self.name = "error_categorizer"

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        golden_execution_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:

        error_category = self.categorize_error(generated_error)
        return 100, error_category

    def categorize_error(self, generated_error: str) -> str:
        if not generated_error:
            return "No Error"

        if "APP_ERROR" in generated_error:
            return "Query Error"
        
        if re.search(r'function .* does not exist', generated_error, re.IGNORECASE):
            return "Wrong Function"
        
        if re.search(r'column .* does not exist', generated_error, re.IGNORECASE):
            return "Column Linking Error"

        if re.search(r'Unknown column', generated_error, re.IGNORECASE):
            return "Column Linking Error"
        
        if re.search(r'operator does not exist', generated_error, re.IGNORECASE):
            return "Wrong Operator"
        
        if re.search(r'relation .* does not exist', generated_error, re.IGNORECASE):
            return "Wrong Relation"

        if re.search(r'Table .* doesn\'t exist', generated_error, re.IGNORECASE):
            return "Wrong Relation"
        
        if "syntax error" in generated_error:
            return "Syntax Error"
        
        if "error in your SQL syntax" in generated_error:
            return "Syntax Error"
        
        if "division by zero" in generated_error:
            return "Division by Zero"
        
        if "zero-length" in generated_error:
            return "Bad Quoting"
        
        if "must appear in the GROUP BY clause or be used in an aggregate function" in generated_error:
            return "Bad Group By"
        
        if "subquery in FROM must have an alias" in generated_error:
            return "Subquery Must Have Alias"
        
        if re.search(r'limit .* not yet support', generated_error, re.IGNORECASE):
            return "Unsupported Operation"
        
        if re.search(r'Column .* in field list is ambiguous', generated_error, re.IGNORECASE):
            return "Column Ambiguous"
        
        if re.search(r'is not a recognized built-in function', generated_error, re.IGNORECASE):
            return "Wrong Function"
        
        if re.search(r'Invalid column name', generated_error, re.IGNORECASE):
            return "Invalid Column Name"
        
        if re.search(r'Incorrect syntax near', generated_error, re.IGNORECASE):
            return "Syntax Error"
        
        if re.search(r'Subquery returned more than 1 value', generated_error, re.IGNORECASE):
            return "Subquery Returned Multiple Values"

        if re.search(r'missing FROM-clause entry for table', generated_error, re.IGNORECASE):
            return "Missing FROM-Clause Entry"
        
        print("No error category found for error: ", generated_error)
        return "Unknown Error"
