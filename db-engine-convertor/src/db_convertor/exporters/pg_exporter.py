"""PostgreSQL database exporter."""

import psycopg2
import csv
from pathlib import Path
from typing import List, Tuple, Dict
from .base import DatabaseExporter


class PostgreSQLExporter(DatabaseExporter):
    """Exporter for PostgreSQL databases."""
    
    def __init__(self, connection: Dict[str, str]):
        """Initialize PostgreSQL exporter.
        
        Args:
            connection: Dict with host, port, user, password, database
        """
        super().__init__(connection)
        self.host = connection['host']
        self.port = connection.get('port', '5432')
        self.user = connection['user']
        self.password = connection['password']
        self.database = connection['database']
        self._conn = None
    
    def _get_connection(self):
        """Get or create database connection."""
        if self._conn is None or self._conn.closed:
            self._conn = psycopg2.connect(
                host=self.host,
                port=self.port,
                user=self.user,
                password=self.password,
                database=self.database
            )
        return self._conn
    
    def close(self):
        """Close database connection."""
        if self._conn and not self._conn.closed:
            self._conn.close()
            self._conn = None
    
    def export_schema(self, output_path: Path) -> str:
        """Export PostgreSQL schema to SQL file.
        
        Args:
            output_path: Path to write schema file
            
        Returns:
            Path to the exported schema file
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        
        schema_statements = []
        
        # Export tables
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = [row[0] for row in cursor.fetchall()]
        
        for table in tables:
            # Get table structure
            cursor.execute(f"""
                SELECT 
                    column_name,
                    data_type,
                    character_maximum_length,
                    is_nullable,
                    column_default
                FROM information_schema.columns
                WHERE table_name = %s
                AND table_schema = 'public'
                ORDER BY ordinal_position
            """, (table,))
            
            columns = cursor.fetchall()
            
            # Build CREATE TABLE statement
            create_stmt = f'CREATE TABLE "{table}" (\n'
            column_defs = []
            
            for col_name, data_type, char_max_len, is_nullable, col_default in columns:
                col_def = f'  "{col_name}" {data_type.upper()}'
                
                if char_max_len:
                    col_def += f'({char_max_len})'
                
                if is_nullable == 'NO':
                    col_def += ' NOT NULL'
                
                if col_default:
                    col_def += f' DEFAULT {col_default}'
                
                column_defs.append(col_def)
            
            create_stmt += ',\n'.join(column_defs)
            
            # Get primary key - use pg_class OID lookup to handle mixed-case table names
            # (%s::regclass folds unquoted names to lowercase and fails on mixed-case tables)
            cursor.execute("""
                SELECT a.attname
                FROM pg_index i
                JOIN pg_attribute a ON a.attrelid = i.indrelid AND a.attnum = ANY(i.indkey)
                WHERE i.indrelid = (
                    SELECT c.oid FROM pg_class c
                    JOIN pg_namespace n ON c.relnamespace = n.oid
                    WHERE c.relname = %s AND n.nspname = 'public'
                ) AND i.indisprimary
            """, (table,))
            pk_columns = [row[0] for row in cursor.fetchall()]
            
            if pk_columns:
                pk_cols_str = '", "'.join(pk_columns)
                pk_def = f',\n  PRIMARY KEY ("{pk_cols_str}")'
                create_stmt += pk_def
            
            create_stmt += '\n);'
            schema_statements.append(create_stmt)
        
        # Get foreign keys
        cursor.execute("""
            SELECT
                tc.table_name,
                kcu.column_name,
                ccu.table_name AS foreign_table_name,
                ccu.column_name AS foreign_column_name,
                tc.constraint_name
            FROM information_schema.table_constraints AS tc
            JOIN information_schema.key_column_usage AS kcu
                ON tc.constraint_name = kcu.constraint_name
                AND tc.table_schema = kcu.table_schema
            JOIN information_schema.constraint_column_usage AS ccu
                ON ccu.constraint_name = tc.constraint_name
                AND ccu.table_schema = tc.table_schema
            WHERE tc.constraint_type = 'FOREIGN KEY'
            AND tc.table_schema = 'public'
        """)
        
        for table, column, ref_table, ref_column, constraint_name in cursor.fetchall():
            fk_stmt = f'ALTER TABLE "{table}" ADD CONSTRAINT "{constraint_name}" ' \
                     f'FOREIGN KEY ("{column}") REFERENCES "{ref_table}" ("{ref_column}");'
            schema_statements.append(fk_stmt)
        
        # Write to file
        output_path = Path(output_path)
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("-- PostgreSQL Database Schema Export\n")
            f.write(f"-- Database: {self.database}\n\n")
            for statement in schema_statements:
                f.write(statement + '\n\n')
        
        cursor.close()
        return str(output_path)
    
    def get_tables(self) -> List[str]:
        """Get list of all tables in the database.
        
        Returns:
            List of table names
        """
        conn = self._get_connection()
        cursor = conn.cursor()
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public' 
            AND table_type = 'BASE TABLE'
            ORDER BY table_name
        """)
        tables = [row[0] for row in cursor.fetchall()]
        cursor.close()
        return tables
    
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
                # Convert None to empty string, and other values to string
                processed_row = ['' if val is None else str(val) for val in row]
                writer.writerow(processed_row)
                row_count += 1
        
        cursor.close()
        return row_count, str(output_path)
    
    def __enter__(self):
        """Context manager entry."""
        return self
    
    def __exit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit."""
        self.close()

