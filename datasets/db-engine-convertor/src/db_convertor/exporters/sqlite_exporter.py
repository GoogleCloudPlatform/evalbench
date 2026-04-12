"""SQLite database exporter."""

import sqlite3
import csv
from pathlib import Path
from typing import List, Tuple
from .base import DatabaseExporter


class SQLiteExporter(DatabaseExporter):
    """Exporter for SQLite databases."""
    
    def __init__(self, database_path: str):
        """Initialize SQLite exporter.
        
        Args:
            database_path: Path to SQLite database file
        """
        super().__init__(database_path)
        self.database_path = database_path
        self._conn = None
    
    def _get_connection(self):
        """Get or create database connection."""
        if self._conn is None:
            self._conn = sqlite3.connect(self.database_path)
        return self._conn
    
    def close(self):
        """Close database connection."""
        if self._conn:
            self._conn.close()
            self._conn = None
    
    def export_schema(self, output_path: Path) -> str:
        """Export SQLite schema to SQL file.
        
        Args:
            output_path: Path to write schema file
            
        Returns:
            Path to the exported schema file
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get all schema objects (tables, views, triggers, indexes)
        cursor.execute("""
            SELECT sql 
            FROM sqlite_master 
            WHERE sql IS NOT NULL
            ORDER BY 
                CASE type
                    WHEN 'table' THEN 1
                    WHEN 'index' THEN 2
                    WHEN 'view' THEN 3
                    WHEN 'trigger' THEN 4
                    ELSE 5
                END,
                name
        """)
        
        schema_statements = []
        for row in cursor.fetchall():
            if row[0]:
                schema_statements.append(row[0] + ';')
        
        # Write to file
        output_path = Path(output_path)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("-- SQLite Database Schema Export\n")
            f.write(f"-- Source: {self.database_path}\n\n")
            for statement in schema_statements:
                f.write(statement + '\n\n')
        
        return str(output_path)
    
    def get_tables(self) -> List[str]:
        """Get list of all tables in the database.
        
        Returns:
            List of table names
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT name 
            FROM sqlite_master 
            WHERE type='table' 
            AND name NOT LIKE 'sqlite_%'
            ORDER BY name
        """)
        return [row[0] for row in cursor.fetchall()]
    
    def export_table_data(self, table_name: str, output_path: Path) -> Tuple[int, str]:
        """Export table data to CSV.
        
        Args:
            table_name: Name of the table to export
            output_path: Path to write CSV file
            
        Returns:
            Tuple of (row_count, csv_path)
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        # Get all data from the table
        cursor.execute(f'SELECT * FROM "{table_name}"')
        
        # Get column names
        column_names = [description[0] for description in cursor.description]
        
        # Write CSV
        output_path = Path(output_path)
        with open(output_path, 'w', newline='', encoding='utf-8') as csv_file:
            writer = csv.writer(csv_file)
            
            # Write header
            writer.writerow(column_names)
            
            # Write data
            row_count = 0
            for row in cursor:
                writer.writerow(row)
                row_count += 1
        
        return row_count, str(output_path)
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

