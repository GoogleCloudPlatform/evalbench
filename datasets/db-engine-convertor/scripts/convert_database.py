#!/usr/bin/env python3
"""CLI tool for database conversion."""

import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from db_convertor.converters.base import ConversionConfig
from db_convertor.converters.sqlite_to_pg import SQLiteToPGConverter
from db_convertor.converters.sqlite_to_mysql import SQLiteToMySQLConverter
from db_convertor.converters.sqlite_to_spanner import SQLiteToSpannerConverter
from db_convertor.converters.pg_to_mysql import PGToMySQLConverter
from db_convertor.converters.pg_to_spanner import PGToSpannerConverter
from db_convertor.converters.pg_to_bigquery import PGToBigQueryConverter
from db_convertor.converters.bq_to_pg import BQToPGConverter
from db_convertor.exporters.sqlite_exporter import SQLiteExporter
from db_convertor.core.orchestrator import ConversionOrchestrator


def export_sqlite(args):
    """Export SQLite database to schema and CSV files."""
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    
    print(f"Exporting SQLite database: {args.database}")
    print(f"Output directory: {output_dir}")
    
    with SQLiteExporter(args.database) as exporter:
        result = exporter.export_all(output_dir)
    
    print(f"\n✓ Export completed!")
    print(f"  Schema: {result['schema_path']}")
    print(f"  Tables: {result['table_count']}")
    print(f"  Total rows: {result['total_rows']}")
    
    return result


def convert_database(args):
    """Convert database from source to target."""
    work_dir = Path(args.work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    
    # Extract database name from source connection or use provided name
    if hasattr(args, 'database_name') and args.database_name:
        database_name = args.database_name
    else:
        # Try to extract from connection string
        if args.source_type == 'sqlite':
            database_name = Path(args.source_connection).stem
        else:
            database_name = args.target_database
    
    # Create conversion config
    config = ConversionConfig(
        source_type=args.source_type,
        target_type=args.target_type,
        source_connection=args.source_connection if args.source_type == 'sqlite' else {
            'host': args.source_host,
            'port': args.source_port,
            'user': args.source_user,
            'password': args.source_password,
            'database': args.source_database,
            'project_id': args.source_project,
            'dataset_id': args.source_database,  # Using source-database as dataset-id for consistency
        },
        target_connection={
            'host': args.target_host,
            'port': args.target_port,
            'user': args.target_user,
            'password': args.target_password,
            'database': args.target_database,
            'database_id': args.target_database,
            'dataset_id': args.target_database,
            'instance_id': args.target_instance,
            'project_id': args.target_project,
        },
        work_dir=work_dir,
        database_name=database_name,
        max_attempts=args.max_attempts,
        streaming=getattr(args, 'streaming', False),
        streaming_workers=getattr(args, 'streaming_workers', 1),
        streaming_batch_size=getattr(args, 'streaming_batch_size', 1000),
    )
    
    # Get appropriate converter
    if config.source_type == 'sqlite' and config.target_type == 'postgresql':
        converter = SQLiteToPGConverter(config)
    elif config.source_type == 'sqlite' and config.target_type == 'mysql':
        converter = SQLiteToMySQLConverter(config)
    elif config.source_type == 'sqlite' and config.target_type == 'spanner':
        converter = SQLiteToSpannerConverter(config)
    elif config.source_type == 'postgresql' and config.target_type == 'mysql':
        converter = PGToMySQLConverter(config)
    elif config.source_type == 'postgresql' and config.target_type == 'spanner':
        converter = PGToSpannerConverter(config)
    elif config.source_type == 'postgresql' and config.target_type == 'bigquery':
        converter = PGToBigQueryConverter(config)
    elif config.source_type == 'bigquery' and config.target_type == 'postgresql':
        converter = BQToPGConverter(config)
    else:
        print(f"Error: Conversion from {config.source_type} to {config.target_type} not yet supported")
        return False
    
    # Run conversion (export is now integrated into orchestrator)
    orchestrator = ConversionOrchestrator(converter)
    success = orchestrator.run_conversion(
        export_source=True,  # Always export as part of the abstraction
        migration_dir=args.migration_dir if hasattr(args, 'migration_dir') and args.migration_dir else None
    )
    
    return success


def replay_migration(args):
    """Replay a migration using existing artifacts."""
    migration_dir = Path(args.migration_dir)
    
    if not migration_dir.exists():
        print(f"Error: Migration directory not found: {migration_dir}")
        return False
    
    # Load configuration
    import json
    config_path = migration_dir / 'config.json'
    if not config_path.exists():
        print(f"Error: config.json not found in {migration_dir}")
        return False
    
    with open(config_path) as f:
        config_data = json.load(f)
    
    print(f"Replaying migration: {migration_dir.name}")
    print(f"Source type: {config_data['source_type']}")
    print(f"Target type: {config_data['target_type']}")
    
    # Create conversion config
    config = ConversionConfig(
        source_type=config_data['source_type'],
        target_type=config_data['target_type'],
        source_connection=args.source_connection if hasattr(args, 'source_connection') else str(migration_dir / 'source'),
        target_connection={
            'host': args.target_host,
            'port': args.target_port,
            'user': args.target_user,
            'password': args.target_password,
            'database': args.target_database,
            'instance_id': args.target_instance,
            'project_id': args.target_project,
        },
        work_dir=migration_dir.parent.parent,
        max_attempts=1  # Only one attempt for replay
    )
    
    # Get converter
    if config.source_type == 'sqlite' and config.target_type == 'postgresql':
        converter = SQLiteToPGConverter(config)
    else:
        print(f"Error: Conversion from {config.source_type} to {config.target_type} not yet supported")
        return False
    
    # Run pipeline with existing artifacts
    from db_convertor.core.pipeline import ConversionPipeline
    
    importer = converter.get_importer()
    pipeline = ConversionPipeline(converter, importer)
    
    schema_file = migration_dir / 'artifacts' / converter.get_schema_filename()
    convertor_file = migration_dir / 'artifacts' / converter.get_convertor_filename()
    source_csv_dir = migration_dir / 'source'
    converted_csv_dir = migration_dir / 'converted'
    
    if not schema_file.exists() or not convertor_file.exists():
        print(f"Error: Artifacts not found in {migration_dir / 'artifacts'}")
        return False
    
    print("\nRunning pipeline with existing artifacts...")
    error = pipeline.run(schema_file, convertor_file, source_csv_dir, converted_csv_dir)
    
    if error is None:
        print("\n✓ Replay successful!")
        return True
    else:
        print(f"\n✗ Replay failed: {error}")
        return False


def main():
    parser = argparse.ArgumentParser(
        description='Database conversion toolkit',
        formatter_class=argparse.RawDescriptionHelpFormatter
    )
    
    subparsers = parser.add_subparsers(dest='command', help='Command to run')
    
    # Export command
    export_parser = subparsers.add_parser('export', help='Export database to schema and CSV files')
    export_parser.add_argument('database', help='Path to database file')
    export_parser.add_argument('-o', '--output-dir', required=True, help='Output directory')
    
    # Convert command
    convert_parser = subparsers.add_parser('convert', help='Convert database from source to target')
    convert_parser.add_argument('--source-type', required=True, choices=['sqlite', 'postgresql', 'mysql', 'bigquery'],
                                help='Source database type')
    convert_parser.add_argument('--target-type', required=True, choices=['postgresql', 'mysql', 'bigquery', 'spanner'],
                                help='Target database type')
    
    # Source connection arguments (SQLite uses path, others use host/port/user/password)
    convert_parser.add_argument('--source-connection',
                                help='Source connection path (for SQLite)')
    convert_parser.add_argument('--source-host', help='Source database host (for PostgreSQL/MySQL)')
    convert_parser.add_argument('--source-port', help='Source database port (for PostgreSQL/MySQL)')
    convert_parser.add_argument('--source-user', help='Source database user (for PostgreSQL/MySQL)')
    convert_parser.add_argument('--source-password', help='Source database password (for PostgreSQL/MySQL)')
    convert_parser.add_argument('--source-database', help='Source database name (for PostgreSQL/MySQL) or dataset ID (for BigQuery)')
    convert_parser.add_argument('--source-project', help='Source Google Cloud project ID (for BigQuery)')
    
    convert_parser.add_argument('--database-name', 
                                help='Database name for migration directory (auto-detected if not provided)')
    convert_parser.add_argument('--target-host', help='Target database host')
    convert_parser.add_argument('--target-port', default='5432', help='Target database port')
    convert_parser.add_argument('--target-user', help='Target database user')
    convert_parser.add_argument('--target-password', help='Target database password')
    convert_parser.add_argument('--target-database', required=True, help='Target database name')
    
    # Spanner specific arguments
    convert_parser.add_argument('--target-instance', help='Target Spanner instance ID')
    convert_parser.add_argument('--target-project', help='Target Spanner project ID')
    
    convert_parser.add_argument('--work-dir', default='.', help='Working directory')
    convert_parser.add_argument('--max-attempts', type=int, default=10, help='Maximum conversion attempts')
    convert_parser.add_argument('--migration-dir', help='Specific migration directory (optional)')

    # Streaming mode (postgresql → mysql only)
    convert_parser.add_argument('--streaming', action='store_true',
                                help='Stream directly from PG to MySQL via psycopg2 (no AI, no CSV). '
                                     'Memory-efficient for large tables. Only for postgresql → mysql.')
    convert_parser.add_argument('--streaming-workers', type=int, default=1,
                                help='Parallel table workers in streaming mode (default: 1)')
    convert_parser.add_argument('--streaming-batch-size', type=int, default=1000,
                                help='Rows per INSERT batch in streaming mode (default: 1000)')
    
    # Replay command
    replay_parser = subparsers.add_parser('replay', help='Replay a migration using existing artifacts')
    replay_parser.add_argument('migration_dir', help='Path to migration directory')
    replay_parser.add_argument('--target-host', required=True, help='Target database host')
    replay_parser.add_argument('--target-port', default='5432', help='Target database port')
    replay_parser.add_argument('--target-user', required=True, help='Target database user')
    replay_parser.add_argument('--target-password', required=True, help='Target database password')
    replay_parser.add_argument('--target-database', required=True, help='Target database name')
    
    args = parser.parse_args()
    
    if not args.command:
        parser.print_help()
        return 1
    
    if args.command == 'export':
        export_sqlite(args)
        return 0
    elif args.command == 'convert':
        success = convert_database(args)
        return 0 if success else 1
    elif args.command == 'replay':
        success = replay_migration(args)
        return 0 if success else 1
    else:
        parser.print_help()
        return 1


if __name__ == '__main__':
    sys.exit(main())

