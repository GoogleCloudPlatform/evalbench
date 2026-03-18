"""Base classes for query conversion between database dialects."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Dict, List, Optional, Tuple
from enum import Enum


class ConversionStatus(Enum):
    """Status of query conversion."""
    RESULT_MATCHED = "result_matched"          # Query converted and results match
    UNABLE_TO_MATCH = "unable_to_match"        # Cannot convert/match the query
    EXHAUSTED_RETRY = "exhausted_retry"        # Max attempts reached without match


@dataclass
class QueryResult:
    """Result of a query execution."""
    columns: List[str]
    rows: List[Tuple]
    total_rows: int
    error: Optional[str] = None
    
    def get_summary(self, num_lines: int = 5) -> str:
        """Get a summary of the query result."""
        if self.error:
            return f"ERROR: {self.error}"
        
        summary = []
        summary.append(f"Total rows: {self.total_rows}")
        summary.append(f"Columns: {', '.join(self.columns)}")
        
        if self.rows:
            summary.append(f"\nFirst {min(num_lines, len(self.rows))} rows:")
            for i, row in enumerate(self.rows[:num_lines], 1):
                summary.append(f"  {i}: {row}")
            
            if len(self.rows) > num_lines:
                summary.append(f"\nLast {num_lines} rows:")
                for i, row in enumerate(self.rows[-num_lines:], 1):
                    summary.append(f"  {i}: {row}")
        
        return '\n'.join(summary)


@dataclass
class ConversionResult:
    """Result of query conversion."""
    status: ConversionStatus
    converted_query: str
    source_result: Optional[QueryResult] = None
    dest_result: Optional[QueryResult] = None
    reason: Optional[str] = None
    attempts: int = 0


class QueryConverter(ABC):
    """Abstract base class for query conversion between dialects."""
    
    def __init__(self, source_dialect: str, dest_dialect: str):
        """Initialize query converter.
        
        Args:
            source_dialect: Source database dialect (e.g., 'sqlite')
            dest_dialect: Destination database dialect (e.g., 'postgresql')
        """
        self.source_dialect = source_dialect
        self.dest_dialect = dest_dialect
    
    @abstractmethod
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
        """Build prompt for query conversion.
        
        Args:
            source_schema: Source database schema
            dest_schema: Destination database schema
            source_query: Original query in source dialect
            converted_query: Previously converted query (for iterations)
            source_result: Result from executing source query
            dest_result: Result from executing converted query
            attempt: Current attempt number
            max_attempts: Maximum number of attempts
            attempt_history: List of previous attempts with query, result, notes
            
        Returns:
            Prompt string for the AI agent
        """
        pass
    
    @abstractmethod
    def compare_results(
        self,
        source_result: QueryResult,
        dest_result: QueryResult
    ) -> Tuple[bool, str]:
        """Compare query results to determine if they match.
        
        Args:
            source_result: Result from source query
            dest_result: Result from destination query
            
        Returns:
            Tuple of (matches: bool, reason: str)
        """
        pass

