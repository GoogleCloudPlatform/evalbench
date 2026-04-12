#!/usr/bin/env python3
"""Unified script to run database conversion examples.

This script orchestrates all conversion examples with:
- Centralized environment configuration
- Prerequisite checking
- Full command logging
- Replay command generation

Usage:
    python3 examples/run_conversion_examples.py --example 1
    python3 examples/run_conversion_examples.py --example 4  # Requires Example 1
    python3 examples/run_conversion_examples.py --all
"""

import os
import sys
import argparse
import subprocess
from pathlib import Path
from typing import Optional, List
from dotenv import load_dotenv


class ExampleRunner:
    """Runs database conversion examples with environment validation."""
    
    def __init__(self, project_root: Path):
        self.project_root = project_root
        self.examples_dir = project_root / "examples"
        self.scripts_dir = project_root / "scripts"
        self.env = {}
        
    def load_environment(self) -> bool:
        """Load and validate environment variables from .env file."""
        env_file = self.project_root / ".env"
        
        if not env_file.exists():
            print(f"❌ Error: .env file not found at {env_file}")
            print(f"📝 Copy .env.example to .env and fill in your values:")
            print(f"   cp .env.example .env")
            return False
        
        # Load .env file
        load_dotenv(env_file)
        
        # Store environment variables
        self.env = dict(os.environ)
        
        return True
    
    def validate_env_vars(self, required_vars: List[str]) -> bool:
        """Validate required environment variables are set."""
        missing = []
        for var in required_vars:
            if not self.env.get(var):
                missing.append(var)
        
        if missing:
            print(f"❌ Error: Missing required environment variables:")
            for var in missing:
                print(f"   - {var}")
            return False
        
        return True
    
    def check_prerequisite(self, example_num: int) -> bool:
        """Check if prerequisite examples have been completed."""
        prerequisites = {
            4: [1],  # Example 4 (PG→MySQL) requires Example 1 (SQLite→PG)
            5: [1],  # Example 5 (PG→Spanner) requires Example 1
            6: [1],  # Example 6 (PG→BQ) requires Example 1
            7: [6],  # Example 7 (BQ→PG) requires Example 6
        }
        
        if example_num not in prerequisites:
            return True
        
        for prereq in prerequisites[example_num]:
            prereq_dir = self._get_example_dir(prereq)
            migrations_dir = prereq_dir / "migrations"
            
            if not migrations_dir.exists() or not any(migrations_dir.iterdir()):
                print(f"❌ Error: Example {example_num} requires Example {prereq} to be completed first")
                print(f"   Run: python3 examples/run_conversion_examples.py --example {prereq}")
                return False
            
            # Check if there's a SUCCESS marker in the latest migration
            latest_migration = self._get_latest_migration(migrations_dir)
            if latest_migration and not (latest_migration / "SUCCESS").exists():
                print(f"⚠️  Warning: Example {prereq} may have failed (no SUCCESS marker)")
                response = input(f"Continue anyway? [y/N]: ")
                if response.lower() != 'y':
                    return False
        
        return True
    
    def _get_example_dir(self, example_num: int) -> Path:
        """Get example directory path."""
        example_names = {
            1: "1_sqlite_to_pg",
            2: "2_sqlite_to_mysql",
            3: "3_sqlite_to_spanner",
            4: "4_pg_to_mysql",
            5: "5_pg_to_spanner",
            6: "6_pg_to_bq",
            7: "7_bq_to_pg",
        }
        return self.examples_dir / example_names[example_num]
    
    def _get_latest_migration(self, migrations_dir: Path) -> Optional[Path]:
        """Get the latest migration directory."""
        if not migrations_dir.exists():
            return None
        
        migration_dirs = [d for d in migrations_dir.iterdir() if d.is_dir()]
        if not migration_dirs:
            return None
        
        # Sort by modification time, return latest
        return max(migration_dirs, key=lambda p: p.stat().st_mtime)
    
    def _extract_converted_queries(self, source_csv: Path, output_csv: Path) -> bool:
        """Extract converted_query column from result CSV to create new source queries.
        
        Takes a query conversion result CSV (with source_query, converted_query columns)
        and creates a new CSV with only successfully converted queries as the new source.
        
        Args:
            source_csv: Path to conversion result CSV
            output_csv: Path to output CSV with extracted queries
            
        Returns:
            True if successful, False otherwise
        """
        import csv
        
        try:
            queries = []
            with open(source_csv, 'r') as f:
                reader = csv.DictReader(f)
                for row in reader:
                    # Only include queries that successfully converted
                    if row.get('conversion_result') == 'result_matched':
                        queries.append({
                            'question_id': row['question_id'],
                            'source_query': row['converted_query']  # Use converted query as new source
                        })
            
            # Write to output CSV
            with open(output_csv, 'w', newline='') as f:
                writer = csv.DictWriter(f, fieldnames=['question_id', 'source_query'])
                writer.writeheader()
                writer.writerows(queries)
            
            print(f"📝 Extracted {len(queries)} successfully converted queries")
            print(f"   From: {source_csv}")
            print(f"   To: {output_csv}")
            return True
            
        except Exception as e:
            print(f"❌ Error extracting queries: {e}")
            return False
    
    def run_example_1(self) -> bool:
        """Example 1: SQLite → PostgreSQL"""
        print("\n" + "="*60)
        print("EXAMPLE 1: SQLite → PostgreSQL")
        print("="*60)
        
        # Validate environment
        required_vars = [
            "SQLITE_DB_PATH", "SQLITE_QUERIES_PATH",
            "PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DB_1"
        ]
        if not self.validate_env_vars(required_vars):
            return False
        
        example_dir = self._get_example_dir(1)
        example_dir.mkdir(exist_ok=True)
        
        migrations_dir = example_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)
        
        print(f"\n📁 Example directory: {example_dir}")
        print(f"📊 Source database: {self.env['SQLITE_DB_PATH']}")
        print(f"📝 Source queries: {self.env['SQLITE_QUERIES_PATH']}")
        print(f"🎯 Target: {self.env['PG_USER']}@{self.env['PG_HOST']}:{self.env['PG_PORT']}/{self.env['PG_DB_1']}")
        
        # Step 1: Data Migration
        print("\n" + "-"*60)
        print("STEP 1: Data Migration")
        print("-"*60)
        
        convert_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "convert",
            "--source-type", "sqlite",
            "--target-type", "postgresql",
            "--source-connection", self.env["SQLITE_DB_PATH"],
            "--target-host", self.env["PG_HOST"],
            "--target-port", self.env["PG_PORT"],
            "--target-user", self.env["PG_USER"],
            "--target-password", self.env["PG_PASSWORD"],
            "--target-database", self.env["PG_DB_1"],
            "--work-dir", str(example_dir),
            "--max-attempts", "10"
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(convert_cmd)}\n")
        
        result = subprocess.run(convert_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("❌ Data migration failed!")
            return False
        
        # Find the latest migration directory
        latest_migration = self._get_latest_migration(migrations_dir)
        if not latest_migration:
            print("❌ No migration directory found!")
            return False
        
        print(f"\n✅ Migration directory: {latest_migration}")
        
        # Step 2: Query Conversion
        print("\n" + "-"*60)
        print("STEP 2: Query Conversion")
        print("-"*60)
        
        query_output = example_dir / "pg_queries.csv"
        
        query_cmd = [
            "python3", str(self.scripts_dir / "convert_queries.py"),
            "--source-type", "sqlite",
            "--target-type", "postgresql",
            "--source-connection", self.env["SQLITE_DB_PATH"],
            "--source-schema", str(latest_migration / "source" / "schema.sql"),
            "--target-schema", str(latest_migration / "artifacts" / "postgresql_schema.sql"),
            "--queries-file", self.env["SQLITE_QUERIES_PATH"],
            "--target-host", self.env["PG_HOST"],
            "--target-port", self.env["PG_PORT"],
            "--target-user", self.env["PG_USER"],
            "--target-password", self.env["PG_PASSWORD"],
            "--target-database", self.env["PG_DB_1"],
            "--max-attempts", "5",
            "--num-workers", "10",
            "--output", str(query_output)
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(query_cmd)}\n")
        
        result = subprocess.run(query_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("⚠️  Query conversion had some failures (this is normal)")
        
        # Print replay command
        print("\n" + "="*60)
        print("✅ EXAMPLE 1 COMPLETED!")
        print("="*60)
        print(f"\n📦 Artifacts saved to:")
        print(f"   - Migration: {latest_migration}")
        print(f"   - Queries: {query_output}")
        
        print(f"\n🔁 To replay this migration:")
        replay_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "replay",
            str(latest_migration),
            "--target-host", self.env["PG_HOST"],
            "--target-port", self.env["PG_PORT"],
            "--target-user", self.env["PG_USER"],
            "--target-password", self.env["PG_PASSWORD"],
            "--target-database", self.env["PG_DB_1"]
        ]
        print(f"   {' '.join(replay_cmd)}")
        
        return True
    
    def run_example_2(self) -> bool:
        """Example 2: SQLite → MySQL"""
        print("\n" + "="*60)
        print("EXAMPLE 2: SQLite → MySQL")
        print("="*60)
        
        # Validate environment
        required_vars = [
            "SQLITE_DB_PATH", "SQLITE_QUERIES_PATH",
            "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DB_1"
        ]
        if not self.validate_env_vars(required_vars):
            return False
        
        example_dir = self._get_example_dir(2)
        example_dir.mkdir(exist_ok=True)
        
        migrations_dir = example_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)
        
        print(f"\n📁 Example directory: {example_dir}")
        print(f"📊 Source database: {self.env['SQLITE_DB_PATH']}")
        print(f"📝 Source queries: {self.env['SQLITE_QUERIES_PATH']}")
        print(f"🎯 Target: {self.env['MYSQL_USER']}@{self.env['MYSQL_HOST']}:{self.env['MYSQL_PORT']}/{self.env['MYSQL_DB_1']}")
        
        # Step 1: Data Migration
        print("\n" + "-"*60)
        print("STEP 1: Data Migration")
        print("-"*60)
        
        convert_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "convert",
            "--source-type", "sqlite",
            "--target-type", "mysql",
            "--source-connection", self.env["SQLITE_DB_PATH"],
            "--target-host", self.env["MYSQL_HOST"],
            "--target-port", self.env["MYSQL_PORT"],
            "--target-user", self.env["MYSQL_USER"],
            "--target-password", self.env["MYSQL_PASSWORD"],
            "--target-database", self.env["MYSQL_DB_1"],
            "--work-dir", str(example_dir),
            "--max-attempts", "10"
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(convert_cmd)}\n")
        
        result = subprocess.run(convert_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("❌ Data migration failed!")
            return False
        
        # Find the latest migration directory
        latest_migration = self._get_latest_migration(migrations_dir)
        if not latest_migration:
            print("❌ No migration directory found!")
            return False
        
        print(f"\n✅ Migration directory: {latest_migration}")
        
        # Step 2: Query Conversion
        print("\n" + "-"*60)
        print("STEP 2: Query Conversion")
        print("-"*60)
        
        query_output = example_dir / "mysql_queries.csv"
        
        query_cmd = [
            "python3", str(self.scripts_dir / "convert_queries.py"),
            "--source-type", "sqlite",
            "--target-type", "mysql",
            "--source-connection", self.env["SQLITE_DB_PATH"],
            "--source-schema", str(latest_migration / "source" / "schema.sql"),
            "--target-schema", str(latest_migration / "artifacts" / "mysql_schema.sql"),
            "--queries-file", self.env["SQLITE_QUERIES_PATH"],
            "--target-host", self.env["MYSQL_HOST"],
            "--target-port", self.env["MYSQL_PORT"],
            "--target-user", self.env["MYSQL_USER"],
            "--target-password", self.env["MYSQL_PASSWORD"],
            "--target-database", self.env["MYSQL_DB_1"],
            "--max-attempts", "5",
            "--num-workers", "10",
            "--output", str(query_output)
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(query_cmd)}\n")
        
        result = subprocess.run(query_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("⚠️  Query conversion had some failures (this is normal)")
        
        # Print replay command
        print("\n" + "="*60)
        print("✅ EXAMPLE 2 COMPLETED!")
        print("="*60)
        print(f"\n📦 Artifacts saved to:")
        print(f"   - Migration: {latest_migration}")
        print(f"   - Queries: {query_output}")
        
        print(f"\n🔁 To replay this migration:")
        replay_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "replay",
            str(latest_migration),
            "--target-host", self.env["MYSQL_HOST"],
            "--target-port", self.env["MYSQL_PORT"],
            "--target-user", self.env["MYSQL_USER"],
            "--target-password", self.env["MYSQL_PASSWORD"],
            "--target-database", self.env["MYSQL_DB_1"]
        ]
        print(f"   {' '.join(replay_cmd)}")
        
        return True
    
    def run_example_3(self) -> bool:
        """Example 3: SQLite → Spanner"""
        print("\n" + "="*60)
        print("EXAMPLE 3: SQLite → Spanner")
        print("="*60)
        
        # Validate environment
        required_vars = [
            "SQLITE_DB_PATH", "SQLITE_QUERIES_PATH",
            "GCP_PROJECT_ID", "SPANNER_INSTANCE", "SPANNER_DB"
        ]
        if not self.validate_env_vars(required_vars):
            return False
        
        example_dir = self._get_example_dir(3)
        example_dir.mkdir(exist_ok=True)
        
        migrations_dir = example_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)
        
        print(f"\n📁 Example directory: {example_dir}")
        print(f"📊 Source database: {self.env['SQLITE_DB_PATH']}")
        print(f"📝 Source queries: {self.env['SQLITE_QUERIES_PATH']}")
        print(f"🎯 Target: Spanner {self.env['GCP_PROJECT_ID']}/{self.env['SPANNER_INSTANCE']}/{self.env['SPANNER_DB']}")
        
        # Step 1: Data Migration
        print("\n" + "-"*60)
        print("STEP 1: Data Migration")
        print("-"*60)
        
        convert_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "convert",
            "--source-type", "sqlite",
            "--target-type", "spanner",
            "--source-connection", self.env["SQLITE_DB_PATH"],
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-instance", self.env["SPANNER_INSTANCE"],
            "--target-database", self.env["SPANNER_DB"],
            "--work-dir", str(example_dir),
            "--max-attempts", "10"
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(convert_cmd)}\n")
        
        result = subprocess.run(convert_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("❌ Data migration failed!")
            return False
        
        # Find the latest migration directory
        latest_migration = self._get_latest_migration(migrations_dir)
        if not latest_migration:
            print("❌ No migration directory found!")
            return False
        
        print(f"\n✅ Migration directory: {latest_migration}")
        
        # Step 2: Query Conversion
        print("\n" + "-"*60)
        print("STEP 2: Query Conversion")
        print("-"*60)
        
        query_output = example_dir / "spanner_queries.csv"
        
        query_cmd = [
            "python3", str(self.scripts_dir / "convert_queries.py"),
            "--source-type", "sqlite",
            "--target-type", "spanner",
            "--source-connection", self.env["SQLITE_DB_PATH"],
            "--source-schema", str(latest_migration / "source" / "schema.sql"),
            "--target-schema", str(latest_migration / "artifacts" / "spanner_schema.sql"),
            "--queries-file", self.env["SQLITE_QUERIES_PATH"],
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-instance", self.env["SPANNER_INSTANCE"],
            "--target-database", self.env["SPANNER_DB"],
            "--target-host", "spanner.googleapis.com",  # Placeholder
            "--target-user", "spanner",  # Placeholder
            "--target-password", "spanner",  # Placeholder
            "--max-attempts", "5",
            "--num-workers", "10",
            "--output", str(query_output)
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(query_cmd)}\n")
        
        result = subprocess.run(query_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("⚠️  Query conversion had some failures (this is normal)")
        
        # Print replay command
        print("\n" + "="*60)
        print("✅ EXAMPLE 3 COMPLETED!")
        print("="*60)
        print(f"\n📦 Artifacts saved to:")
        print(f"   - Migration: {latest_migration}")
        print(f"   - Queries: {query_output}")
        
        print(f"\n🔁 To replay this migration:")
        replay_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "replay",
            str(latest_migration),
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-instance", self.env["SPANNER_INSTANCE"],
            "--target-database", self.env["SPANNER_DB"],
            "--target-host", "spanner.googleapis.com",
            "--target-user", "spanner",
            "--target-password", "spanner"
        ]
        print(f"   {' '.join(replay_cmd)}")
        
        return True
    
    def run_example_4(self) -> bool:
        """Example 4: PostgreSQL → MySQL"""
        print("\n" + "="*60)
        print("EXAMPLE 4: PostgreSQL → MySQL")
        print("="*60)
        
        # Validate environment
        required_vars = [
            "PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DB_1",
            "MYSQL_HOST", "MYSQL_PORT", "MYSQL_USER", "MYSQL_PASSWORD", "MYSQL_DB_2"
        ]
        if not self.validate_env_vars(required_vars):
            return False
        
        # Check that Example 1 has been completed and find the converted queries
        example_1_dir = self._get_example_dir(1)
        pg_queries_result_file = example_1_dir / "pg_queries.csv"
        
        if not pg_queries_result_file.exists():
            print(f"❌ Error: PostgreSQL queries file not found: {pg_queries_result_file}")
            print(f"   Make sure Example 1 has been completed successfully")
            return False
        
        example_dir = self._get_example_dir(4)
        example_dir.mkdir(exist_ok=True)
        
        migrations_dir = example_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)
        
        # Extract PostgreSQL queries (converted_query from Example 1) as source for this conversion
        print("\n📝 Extracting PostgreSQL queries from Example 1...")
        pg_queries_file = example_dir / "pg_source_queries.csv"
        if not self._extract_converted_queries(pg_queries_result_file, pg_queries_file):
            return False
        
        print(f"\n📁 Example directory: {example_dir}")

        print(f"📊 Source database: {self.env['PG_USER']}@{self.env['PG_HOST']}:{self.env['PG_PORT']}/{self.env['PG_DB_1']}")
        print(f"📝 Source queries: {pg_queries_file}")
        print(f"🎯 Target: {self.env['MYSQL_USER']}@{self.env['MYSQL_HOST']}:{self.env['MYSQL_PORT']}/{self.env['MYSQL_DB_2']}")
        
        # Step 1: Data Migration
        print("\n" + "-"*60)
        print("STEP 1: Data Migration")
        print("-"*60)
        
        convert_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "convert",
            "--source-type", "postgresql",
            "--source-host", self.env["PG_HOST"],
            "--source-port", self.env["PG_PORT"],
            "--source-user", self.env["PG_USER"],
            "--source-password", self.env["PG_PASSWORD"],
            "--source-database", self.env["PG_DB_1"],
            "--target-type", "mysql",
            "--target-host", self.env["MYSQL_HOST"],
            "--target-port", self.env["MYSQL_PORT"],
            "--target-user", self.env["MYSQL_USER"],
            "--target-password", self.env["MYSQL_PASSWORD"],
            "--target-database", self.env["MYSQL_DB_2"],
            "--work-dir", str(example_dir),
            "--max-attempts", "10"
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(convert_cmd)}\n")
        
        result = subprocess.run(convert_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("❌ Data migration failed!")
            return False
        
        # Find the latest migration directory
        latest_migration = self._get_latest_migration(migrations_dir)
        if not latest_migration:
            print("❌ No migration directory found!")
            return False
        
        print(f"\n✅ Migration directory: {latest_migration}")
        
        # Step 2: Query Conversion
        print("\n" + "-"*60)
        print("STEP 2: Query Conversion")
        print("-"*60)
        
        query_output = example_dir / "mysql_from_pg_queries.csv"
        
        query_cmd = [
            "python3", str(self.scripts_dir / "convert_queries.py"),
            "--source-type", "postgresql",
            "--target-type", "mysql",
            "--source-host", self.env["PG_HOST"],
            "--source-port", self.env["PG_PORT"],
            "--source-user", self.env["PG_USER"],
            "--source-password", self.env["PG_PASSWORD"],
            "--source-database", self.env["PG_DB_1"],
            "--source-schema", str(latest_migration / "source" / "schema.sql"),
            "--target-schema", str(latest_migration / "artifacts" / "mysql_schema.sql"),
            "--queries-file", str(pg_queries_file),
            "--target-host", self.env["MYSQL_HOST"],
            "--target-port", self.env["MYSQL_PORT"],
            "--target-user", self.env["MYSQL_USER"],
            "--target-password", self.env["MYSQL_PASSWORD"],
            "--target-database", self.env["MYSQL_DB_2"],
            "--max-attempts", "5",
            "--num-workers", "10",
            "--output", str(query_output)
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(query_cmd)}\n")
        
        result = subprocess.run(query_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("⚠️  Query conversion had some failures (this is normal)")
        
        # Print replay command
        print("\n" + "="*60)
        print("✅ EXAMPLE 4 COMPLETED!")
        print("="*60)
        print(f"\n📦 Artifacts saved to:")
        print(f"   - Migration: {latest_migration}")
        print(f"   - Queries: {query_output}")
        
        print(f"\n🔁 To replay this migration:")
        replay_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "replay",
            str(latest_migration),
            "--target-host", self.env["MYSQL_HOST"],
            "--target-port", self.env["MYSQL_PORT"],
            "--target-user", self.env["MYSQL_USER"],
            "--target-password", self.env["MYSQL_PASSWORD"],
            "--target-database", self.env["MYSQL_DB_2"]
        ]
        print(f"   {' '.join(replay_cmd)}")
        
        return True
    
    def run_example_5(self) -> bool:
        """Example 5: PostgreSQL → Spanner"""
        print("\n" + "="*60)
        print("EXAMPLE 5: PostgreSQL → Spanner")
        print("="*60)
        
        # Validate environment
        required_vars = [
            "PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DB_1",
            "GCP_PROJECT_ID", "SPANNER_INSTANCE", "SPANNER_DB"
        ]
        if not self.validate_env_vars(required_vars):
            return False
        
        # Check that Example 1 has been completed and find the converted queries
        example_1_dir = self._get_example_dir(1)
        pg_queries_result_file = example_1_dir / "pg_queries.csv"
        
        if not pg_queries_result_file.exists():
            print(f"❌ Error: PostgreSQL queries file not found: {pg_queries_result_file}")
            print(f"   Make sure Example 1 has been completed successfully")
            return False
        
        example_dir = self._get_example_dir(5)
        example_dir.mkdir(exist_ok=True)
        
        migrations_dir = example_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)
        
        # Extract PostgreSQL queries (converted_query from Example 1) as source for this conversion
        print("\n📝 Extracting PostgreSQL queries from Example 1...")
        pg_queries_file = example_dir / "pg_source_queries.csv"
        if not self._extract_converted_queries(pg_queries_result_file, pg_queries_file):
            return False
        
        print(f"\n📁 Example directory: {example_dir}")
        print(f"📊 Source database: {self.env['PG_USER']}@{self.env['PG_HOST']}:{self.env['PG_PORT']}/{self.env['PG_DB_1']}")
        print(f"📝 Source queries: {pg_queries_file}")
        print(f"🎯 Target: Spanner {self.env['GCP_PROJECT_ID']}/{self.env['SPANNER_INSTANCE']}/{self.env['SPANNER_DB']}")
        
        # Step 1: Data Migration
        print("\n" + "-"*60)
        print("STEP 1: Data Migration")
        print("-"*60)
        
        convert_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "convert",
            "--source-type", "postgresql",
            "--source-host", self.env["PG_HOST"],
            "--source-port", self.env["PG_PORT"],
            "--source-user", self.env["PG_USER"],
            "--source-password", self.env["PG_PASSWORD"],
            "--source-database", self.env["PG_DB_1"],
            "--target-type", "spanner",
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-instance", self.env["SPANNER_INSTANCE"],
            "--target-database", self.env["SPANNER_DB"],
            "--work-dir", str(example_dir),
            "--max-attempts", "10"
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(convert_cmd)}\n")
        
        result = subprocess.run(convert_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("❌ Data migration failed!")
            return False
        
        # Find the latest migration directory
        latest_migration = self._get_latest_migration(migrations_dir)
        if not latest_migration:
            print("❌ No migration directory found!")
            return False
        
        print(f"\n✅ Migration directory: {latest_migration}")
        
        # Step 2: Query Conversion
        print("\n" + "-"*60)
        print("STEP 2: Query Conversion")
        print("-"*60)
        
        query_output = example_dir / "spanner_from_pg_queries.csv"
        
        query_cmd = [
            "python3", str(self.scripts_dir / "convert_queries.py"),
            "--source-type", "postgresql",
            "--target-type", "spanner",
            "--source-host", self.env["PG_HOST"],
            "--source-port", self.env["PG_PORT"],
            "--source-user", self.env["PG_USER"],
            "--source-password", self.env["PG_PASSWORD"],
            "--source-database", self.env["PG_DB_1"],
            "--source-schema", str(latest_migration / "source" / "schema.sql"),
            "--target-schema", str(latest_migration / "artifacts" / "spanner_schema.sql"),
            "--queries-file", str(pg_queries_file),
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-instance", self.env["SPANNER_INSTANCE"],
            "--target-database", self.env["SPANNER_DB"],
            "--target-host", "spanner.googleapis.com",
            "--target-user", "spanner",
            "--target-password", "spanner",
            "--max-attempts", "5",
            "--num-workers", "10",
            "--output", str(query_output)
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(query_cmd)}\n")
        
        result = subprocess.run(query_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("⚠️  Query conversion had some failures (this is normal)")
        
        # Print replay command
        print("\n" + "="*60)
        print("✅ EXAMPLE 5 COMPLETED!")
        print("="*60)
        print(f"\n📦 Artifacts saved to:")
        print(f"   - Migration: {latest_migration}")
        print(f"   - Queries: {query_output}")
        
        print(f"\n🔁 To replay this migration:")
        replay_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "replay",
            str(latest_migration),
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-instance", self.env["SPANNER_INSTANCE"],
            "--target-database", self.env["SPANNER_DB"],
            "--target-host", "spanner.googleapis.com",
            "--target-user", "spanner",
            "--target-password", "spanner"
        ]
        print(f"   {' '.join(replay_cmd)}")
        
        return True
    
    def run_example_6(self) -> bool:
        """Example 6: PostgreSQL → BigQuery"""
        print("\n" + "="*60)
        print("EXAMPLE 6: PostgreSQL → BigQuery")
        print("="*60)
        
        # Validate environment
        required_vars = [
            "PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DB_1",
            "GCP_PROJECT_ID", "BQ_DATASET"
        ]
        if not self.validate_env_vars(required_vars):
            return False
        
        # Check that Example 1 has been completed and find the converted queries
        example_1_dir = self._get_example_dir(1)
        pg_queries_result_file = example_1_dir / "pg_queries.csv"
        
        if not pg_queries_result_file.exists():
            print(f"❌ Error: PostgreSQL queries file not found: {pg_queries_result_file}")
            print(f"   Make sure Example 1 has been completed successfully")
            return False
        
        example_dir = self._get_example_dir(6)
        example_dir.mkdir(exist_ok=True)
        
        migrations_dir = example_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)
        
        # Extract PostgreSQL queries (converted_query from Example 1) as source for this conversion
        print("\n📝 Extracting PostgreSQL queries from Example 1...")
        pg_queries_file = example_dir / "pg_source_queries.csv"
        if not self._extract_converted_queries(pg_queries_result_file, pg_queries_file):
            return False
        
        print(f"\n📁 Example directory: {example_dir}")
        print(f"📊 Source database: {self.env['PG_USER']}@{self.env['PG_HOST']}:{self.env['PG_PORT']}/{self.env['PG_DB_1']}")
        print(f"📝 Source queries: {pg_queries_file}")
        print(f"🎯 Target: BigQuery {self.env['GCP_PROJECT_ID']}/{self.env['BQ_DATASET']}")
        
        # Step 1: Data Migration
        print("\n" + "-"*60)
        print("STEP 1: Data Migration")
        print("-"*60)
        
        convert_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "convert",
            "--source-type", "postgresql",
            "--source-host", self.env["PG_HOST"],
            "--source-port", self.env["PG_PORT"],
            "--source-user", self.env["PG_USER"],
            "--source-password", self.env["PG_PASSWORD"],
            "--source-database", self.env["PG_DB_1"],
            "--target-type", "bigquery",
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-database", self.env["BQ_DATASET"],
            "--work-dir", str(example_dir),
            "--max-attempts", "10"
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(convert_cmd)}\n")
        
        result = subprocess.run(convert_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("❌ Data migration failed!")
            return False
        
        # Find the latest migration directory
        latest_migration = self._get_latest_migration(migrations_dir)
        if not latest_migration:
            print("❌ No migration directory found!")
            return False
        
        print(f"\n✅ Migration directory: {latest_migration}")
        
        # Step 2: Query Conversion
        print("\n" + "-"*60)
        print("STEP 2: Query Conversion")
        print("-"*60)
        
        query_output = example_dir / "bq_queries.csv"
        
        query_cmd = [
            "python3", str(self.scripts_dir / "convert_queries.py"),
            "--source-type", "postgresql",
            "--target-type", "bigquery",
            "--source-host", self.env["PG_HOST"],
            "--source-port", self.env["PG_PORT"],
            "--source-user", self.env["PG_USER"],
            "--source-password", self.env["PG_PASSWORD"],
            "--source-database", self.env["PG_DB_1"],
            "--source-schema", str(latest_migration / "source" / "schema.sql"),
            "--target-schema", str(latest_migration / "artifacts" / "bigquery_schema.sql"),
            "--queries-file", str(pg_queries_file),
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-database", self.env["BQ_DATASET"],
            "--target-host", "bigquery.googleapis.com",
            "--target-user", "bigquery",
            "--target-password", "bigquery",
            "--max-attempts", "5",
            "--num-workers", "10",
            "--output", str(query_output)
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(query_cmd)}\n")
        
        result = subprocess.run(query_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("⚠️  Query conversion had some failures (this is normal)")
        
        # Print replay command
        print("\n" + "="*60)
        print("✅ EXAMPLE 6 COMPLETED!")
        print("="*60)
        print(f"\n📦 Artifacts saved to:")
        print(f"   - Migration: {latest_migration}")
        print(f"   - Queries: {query_output}")
        
        print(f"\n🔁 To replay this migration:")
        replay_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "replay",
            str(latest_migration),
            "--target-project", self.env["GCP_PROJECT_ID"],
            "--target-database", self.env["BQ_DATASET"],
            "--target-host", "bigquery.googleapis.com",
            "--target-user", "bigquery",
            "--target-password", "bigquery"
        ]
        print(f"   {' '.join(replay_cmd)}")
        
        return True
    
    def run_example_7(self) -> bool:
        """Example 7: BigQuery → PostgreSQL"""
        print("\n" + "="*60)
        print("EXAMPLE 7: BigQuery → PostgreSQL")
        print("="*60)
        
        # Validate environment
        required_vars = [
            "GCP_PROJECT_ID", "BQ_DATASET",
            "PG_HOST", "PG_PORT", "PG_USER", "PG_PASSWORD", "PG_DB_2"
        ]
        if not self.validate_env_vars(required_vars):
            return False
        
        # Check that Example 6 has been completed and find the converted queries
        example_6_dir = self._get_example_dir(6)
        bq_queries_result_file = example_6_dir / "bq_queries.csv"
        
        if not bq_queries_result_file.exists():
            print(f"❌ Error: BigQuery queries file not found: {bq_queries_result_file}")
            print(f"   Make sure Example 6 has been completed successfully")
            return False
        
        example_dir = self._get_example_dir(7)
        example_dir.mkdir(exist_ok=True)
        
        migrations_dir = example_dir / "migrations"
        migrations_dir.mkdir(exist_ok=True)
        
        # Extract BigQuery queries (converted_query from Example 6) as source for this conversion
        print("\n📝 Extracting BigQuery queries from Example 6...")
        bq_queries_file = example_dir / "bq_source_queries.csv"
        if not self._extract_converted_queries(bq_queries_result_file, bq_queries_file):
            return False
        
        print(f"\n📁 Example directory: {example_dir}")
        print(f"📊 Source database: BigQuery {self.env['GCP_PROJECT_ID']}/{self.env['BQ_DATASET']}")
        print(f"📝 Source queries: {bq_queries_file}")
        print(f"🎯 Target: {self.env['PG_USER']}@{self.env['PG_HOST']}:{self.env['PG_PORT']}/{self.env['PG_DB_2']}")
        
        # Step 1: Data Migration
        print("\n" + "-"*60)
        print("STEP 1: Data Migration")
        print("-"*60)
        
        convert_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "convert",
            "--source-type", "bigquery",
            "--source-project", self.env["GCP_PROJECT_ID"],
            "--source-database", self.env["BQ_DATASET"],
            "--target-type", "postgresql",
            "--target-host", self.env["PG_HOST"],
            "--target-port", self.env["PG_PORT"],
            "--target-user", self.env["PG_USER"],
            "--target-password", self.env["PG_PASSWORD"],
            "--target-database", self.env["PG_DB_2"],
            "--work-dir", str(example_dir),
            "--max-attempts", "10"
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(convert_cmd)}\n")
        
        result = subprocess.run(convert_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("❌ Data migration failed!")
            return False
        
        # Find the latest migration directory
        latest_migration = self._get_latest_migration(migrations_dir)
        if not latest_migration:
            print("❌ No migration directory found!")
            return False
        
        print(f"\n✅ Migration directory: {latest_migration}")
        
        # Step 2: Query Conversion
        print("\n" + "-"*60)
        print("STEP 2: Query Conversion")
        print("-"*60)
        
        query_output = example_dir / "pg_from_bq_queries.csv"
        
        query_cmd = [
            "python3", str(self.scripts_dir / "convert_queries.py"),
            "--source-type", "bigquery",
            "--target-type", "postgresql",
            "--source-project", self.env["GCP_PROJECT_ID"],
            "--source-database", self.env["BQ_DATASET"],
            "--source-schema", str(latest_migration / "source" / "schema.sql"),
            "--target-schema", str(latest_migration / "artifacts" / "postgresql_schema.sql"),
            "--queries-file", str(bq_queries_file),
            "--target-host", self.env["PG_HOST"],
            "--target-port", self.env["PG_PORT"],
            "--target-user", self.env["PG_USER"],
            "--target-password", self.env["PG_PASSWORD"],
            "--target-database", self.env["PG_DB_2"],
            "--max-attempts", "5",
            "--num-workers", "10",
            "--output", str(query_output)
        ]
        
        print("\n🔧 Running command:")
        print(f"   {' '.join(query_cmd)}\n")
        
        result = subprocess.run(query_cmd, cwd=self.project_root)
        if result.returncode != 0:
            print("⚠️  Query conversion had some failures (this is normal)")
        
        # Print replay command
        print("\n" + "="*60)
        print("✅ EXAMPLE 7 COMPLETED!")
        print("="*60)
        print(f"\n📦 Artifacts saved to:")
        print(f"   - Migration: {latest_migration}")
        print(f"   - Queries: {query_output}")
        
        print(f"\n🔁 To replay this migration:")
        replay_cmd = [
            "python3", str(self.scripts_dir / "convert_database.py"),
            "replay",
            str(latest_migration),
            "--target-host", self.env["PG_HOST"],
            "--target-port", self.env["PG_PORT"],
            "--target-user", self.env["PG_USER"],
            "--target-password", self.env["PG_PASSWORD"],
            "--target-database", self.env["PG_DB_2"]
        ]
        print(f"   {' '.join(replay_cmd)}")
        
        return True
    
    def run_example(self, example_num: int) -> bool:
        """Run a specific example."""
        # Check prerequisites
        if not self.check_prerequisite(example_num):
            return False
        
        # Run the example
        examples = {
            1: self.run_example_1,
            2: self.run_example_2,
            3: self.run_example_3,
            4: self.run_example_4,
            5: self.run_example_5,
            6: self.run_example_6,
            7: self.run_example_7,
        }
        
        if example_num not in examples:
            print(f"❌ Error: Invalid example number: {example_num}")
            return False
        
        return examples[example_num]()
    
    def run_all(self) -> bool:
        """Run all examples in sequence."""
        for i in range(1, 8):
            if not self.run_example(i):
                print(f"\n❌ Failed at Example {i}")
                return False
        return True


def main():
    parser = argparse.ArgumentParser(
        description="Run database conversion examples",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python3 examples/run_conversion_examples.py --example 1
  python3 examples/run_conversion_examples.py --example 4
  python3 examples/run_conversion_examples.py --all

Available Examples:
  1. SQLite → PostgreSQL
  2. SQLite → MySQL
  3. SQLite → Spanner (requires Spanner support)
  4. PostgreSQL → MySQL (requires Example 1)
  5. PostgreSQL → Spanner (requires Example 1 and Spanner support)
  6. PostgreSQL → BigQuery (requires Example 1 and BigQuery support)
  7. BigQuery → PostgreSQL (requires Example 6)
        """
    )
    
    parser.add_argument(
        "--example",
        type=int,
        choices=range(1, 8),
        help="Run a specific example (1-7)"
    )
    
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all examples in sequence"
    )
    
    args = parser.parse_args()
    
    if not args.example and not args.all:
        parser.print_help()
        sys.exit(1)
    
    # Get project root (parent of examples directory)
    project_root = Path(__file__).parent.parent.absolute()
    
    # Create runner
    runner = ExampleRunner(project_root)
    
    # Load environment
    if not runner.load_environment():
        sys.exit(1)
    
    # Run examples
    if args.all:
        success = runner.run_all()
    else:
        success = runner.run_example(args.example)
    
    sys.exit(0 if success else 1)


if __name__ == "__main__":
    main()
