"""Base classes for database importers."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List


class DatabaseImporter(ABC):
    """Abstract base class for database importers."""
    
    def __init__(self, connection_config: Dict[str, str]):
        """Initialize importer with connection configuration.
        
        Args:
            connection_config: Database connection configuration dict
        """
        self.connection_config = connection_config
    
    @abstractmethod
    def wipe_database(self):
        """Wipe all tables from the database."""
        pass
    
    @abstractmethod
    def create_schema(self, schema_file: Path):
        """Create schema in the database.
        
        Args:
            schema_file: Path to schema SQL file
        """
        pass
    
    @abstractmethod
    def load_csv_data(self, csv_dir: Path, tables: List[str]):
        """Load CSV data into database tables.
        
        Args:
            csv_dir: Directory containing CSV files
            tables: List of tables in dependency order
        """
        pass
    
    @abstractmethod
    def get_table_dependencies(self) -> List[str]:
        """Get tables in dependency order based on foreign keys.
        
        Returns:
            List of table names in load order
        """
        pass
    
    @abstractmethod
    def verify_row_counts(self, expected_counts: Dict[str, int]) -> bool:
        """Verify row counts match expected values.
        
        Args:
            expected_counts: Dict mapping table names to expected row counts
            
        Returns:
            True if all counts match, False otherwise
        """
        pass

