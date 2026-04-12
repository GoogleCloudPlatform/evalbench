"""Base classes for database exporters."""

from abc import ABC, abstractmethod
from pathlib import Path
from typing import Dict, List, Tuple


class DatabaseExporter(ABC):
    """Abstract base class for database exporters."""
    
    def __init__(self, connection_string: str):
        """Initialize exporter with connection string.
        
        Args:
            connection_string: Database connection string
        """
        self.connection_string = connection_string
    
    @abstractmethod
    def export_schema(self, output_path: Path) -> str:
        """Export database schema to a file.
        
        Args:
            output_path: Path to write schema file
            
        Returns:
            Path to the exported schema file
        """
        pass
    
    @abstractmethod
    def export_table_data(self, table_name: str, output_path: Path) -> Tuple[int, str]:
        """Export table data to CSV.
        
        Args:
            table_name: Name of the table to export
            output_path: Path to write CSV file
            
        Returns:
            Tuple of (row_count, csv_path)
        """
        pass
    
    @abstractmethod
    def get_tables(self) -> List[str]:
        """Get list of all tables in the database.
        
        Returns:
            List of table names
        """
        pass
    
    def export_all(self, output_dir: Path) -> Dict[str, any]:
        """Export entire database (schema + all tables).
        
        Args:
            output_dir: Directory to write all exports
            
        Returns:
            Dictionary with export metadata
        """
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        # Export schema
        schema_path = self.export_schema(output_dir / 'schema.sql')
        
        # Export tables
        tables = self.get_tables()
        table_exports = {}
        total_rows = 0
        
        for table in tables:
            row_count, csv_path = self.export_table_data(
                table, 
                output_dir / f'{table}.csv'
            )
            table_exports[table] = {
                'rows': row_count,
                'csv_path': csv_path
            }
            total_rows += row_count
        
        return {
            'schema_path': schema_path,
            'tables': table_exports,
            'total_rows': total_rows,
            'table_count': len(tables)
        }

