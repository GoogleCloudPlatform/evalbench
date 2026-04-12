"""Base classes for database converters."""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional


@dataclass
class ConversionConfig:
    """Configuration for a database conversion."""

    source_type: str  # e.g., 'sqlite', 'postgresql', 'mysql'
    target_type: str  # e.g., 'postgresql', 'mysql', 'bigquery'
    source_connection: str
    target_connection: Dict[str, str]
    work_dir: Path
    database_name: str  # Name of the database being converted
    max_attempts: int = 10

    # Streaming mode (pg→mysql only): skip AI+CSV pipeline, stream directly via psycopg2
    streaming: bool = False
    streaming_workers: int = 1
    streaming_batch_size: int = 1000
    
    def get_migration_name(self) -> str:
        """Get migration directory name."""
        from datetime import datetime
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        return f"{self.source_type}_to_{self.target_type}_{self.database_name}_{timestamp}"


class DatabaseConverter(ABC):
    """Abstract base class for database-specific conversion logic."""
    
    def __init__(self, config: ConversionConfig):
        """Initialize converter.
        
        Args:
            config: Conversion configuration
        """
        self.config = config
        self.source_type = config.source_type
        self.target_type = config.target_type
    
    @abstractmethod
    def get_schema_conversion_prompt(self, source_schema: str, 
                                     csv_summaries: Dict, 
                                     prev_schema: Optional[str] = None,
                                     prev_convertor: Optional[str] = None,
                                     error: Optional[str] = None) -> str:
        """Build prompt for schema conversion.
        
        Args:
            source_schema: Source database schema
            csv_summaries: Summary of CSV data
            prev_schema: Previously generated target schema
            prev_convertor: Previously generated convertor script
            error: Error from previous attempt
            
        Returns:
            Prompt string for the AI agent
        """
        pass
    
    @abstractmethod
    def get_exporter(self):
        """Get appropriate exporter for source database.
        
        Returns:
            DatabaseExporter instance
        """
        pass
    
    @abstractmethod
    def get_importer(self):
        """Get appropriate importer for target database.
        
        Returns:
            DatabaseImporter instance
        """
        pass
    
    def get_schema_filename(self) -> str:
        """Get target schema filename."""
        return f"{self.target_type}_schema.sql"
    
    def get_convertor_filename(self) -> str:
        """Get data convertor script filename."""
        return "data_convertor.py"

