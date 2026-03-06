#!/usr/bin/env python3
"""CLI tool for query conversion between database dialects."""

import sys
import argparse
from pathlib import Path

# Add src to path
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from db_convertor.query_converters.sqlite_to_pg import SQLiteToPGQueryConverter
from db_convertor.query_converters.sqlite_to_mysql import SQLiteToMySQLQueryConverter
from db_convertor.query_converters.pg_to_mysql import PGToMySQLQueryConverter
from db_convertor.query_converters.sqlite_to_spanner import SQLiteToSpannerQueryConverter
from db_convertor.query_converters.pg_to_spanner import PostgreSQLToSpannerQueryConverter
from db_convertor.query_converters.pg_to_bigquery import PostgreSQLToBigQueryQueryConverter
from db_convertor.query_converters.bigquery_to_pg import BigQueryToPGQueryConverter
from db_convertor.query_conversion_orchestrator import QueryConversionOrchestrator


def main():
    parser = argparse.ArgumentParser(
        description='Convert SQL queries between database dialects',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Convert queries from file
  python3 scripts/convert_queries.py \\
      --source-type sqlite \\
      --target-type postgresql \\
      --source-connection /path/to/database.sqlite \\
      --source-schema source_schema.sql \\
      --target-schema target_schema.sql \\
      --queries-file queries.txt \\
      --target-host localhost \\
      --target-user postgres \\
      --target-password password \\
      --target-database mydb \\
      --output results.json
  
  # Convert single query
  python3 scripts/convert_queries.py \\
      --source-type sqlite \\
      --target-type postgresql \\
      --source-connection /path/to/database.sqlite \\
      --source-schema source_schema.sql \\
      --target-schema target_schema.sql \\
      --query "SELECT * FROM table" \\
      --target-host localhost \\
      --target-user postgres \\
      --target-password password \\
      --target-database mydb
        """
    )
    
    # Source
    parser.add_argument('--source-type', required=True, choices=['sqlite', 'postgresql', 'mysql', 'bigquery'],
                       help='Source database type')
    parser.add_argument('--source-connection',
                       help='Source database connection path (for SQLite)')
    parser.add_argument('--source-host', help='Source database host (for PostgreSQL/MySQL)')
    parser.add_argument('--source-port', help='Source database port (for PostgreSQL/MySQL)')
    parser.add_argument('--source-user', help='Source database user (for PostgreSQL/MySQL)')
    parser.add_argument('--source-password', help='Source database password (for PostgreSQL/MySQL)')
    parser.add_argument('--source-database', help='Source database name (for PostgreSQL/MySQL) or dataset ID (for BigQuery)')
    parser.add_argument('--source-project', help='Source Google Cloud project ID (for BigQuery)')
    parser.add_argument('--source-schema', required=True,
                       help='Path to source schema file')
    
    # Target
    parser.add_argument('--target-type', required=True, choices=['postgresql', 'mysql', 'spanner', 'bigquery'],
                       help='Target database type')
    parser.add_argument('--target-schema', required=True,
                       help='Path to target schema file')
    parser.add_argument('--target-host', required=True,
                       help='Target database host')
    parser.add_argument('--target-port', default='5432',
                       help='Target database port')
    parser.add_argument('--target-user', required=True,
                       help='Target database user')
    parser.add_argument('--target-password', required=True,
                       help='Target database password')
    parser.add_argument('--target-database', required=True,
                       help='Target database name')
    parser.add_argument('--target-instance', help='Target Spanner instance ID')
    parser.add_argument('--target-project', help='Target Spanner project ID')
    
    # Queries
    parser.add_argument('--query',
                       help='Single query to convert')
    parser.add_argument('--queries-file',
                       help='File containing queries (one per line)')
    
    # Options
    parser.add_argument('--max-attempts', type=int, default=10,
                       help='Maximum attempts per query (default: 10)')
    parser.add_argument('--num-workers', type=int, default=1,
                       help='Number of parallel workers (default: 1)')
    parser.add_argument('--output',
                       help='Output file for conversion results (JSON)')
    
    args = parser.parse_args()
    
    # Validate query input
    if not args.query and not args.queries_file:
        parser.error("Either --query or --queries-file must be provided")
    
    # Read schemas
    with open(args.source_schema) as f:
        source_schema = f.read()
    
    with open(args.target_schema) as f:
        target_schema = f.read()
    
    # Read queries
    queries = []
    if args.query:
        queries.append(('0', args.query))  # Use '0' as question_id for single query
    if args.queries_file:
        # Check if CSV or text file
        if args.queries_file.endswith('.csv'):
            queries = QueryConversionOrchestrator.load_queries_from_csv(Path(args.queries_file))
        else:
            # Plain text file (one query per line)
            with open(args.queries_file) as f:
                for i, line in enumerate(f):
                    line = line.strip()
                    if line and not line.startswith('--') and not line.startswith('#'):
                        queries.append((str(i), line))
    
    if not queries:
        print("Error: No queries to convert")
        return 1
    
    print(f"Loaded {len(queries)} queries to convert")
    
    # Get converter
    if args.source_type == 'sqlite' and args.target_type == 'postgresql':
        converter = SQLiteToPGQueryConverter()
    elif args.source_type == 'sqlite' and args.target_type == 'mysql':
        converter = SQLiteToMySQLQueryConverter()
    elif args.source_type == 'postgresql' and args.target_type == 'mysql':
        converter = PGToMySQLQueryConverter()
    elif args.source_type == 'sqlite' and args.target_type == 'spanner':
        converter = SQLiteToSpannerQueryConverter()
    elif args.source_type == 'postgresql' and args.target_type == 'spanner':
        converter = PostgreSQLToSpannerQueryConverter()
    elif args.source_type == 'postgresql' and args.target_type == 'bigquery':
        converter = PostgreSQLToBigQueryQueryConverter()
    elif args.source_type == 'bigquery' and args.target_type == 'postgresql':
        converter = BigQueryToPGQueryConverter()
    else:
        print(f"Error: Conversion from {args.source_type} to {args.target_type} not supported yet")
        return 1
    
    # Set up source connection
    if args.source_type == 'sqlite':
        source_connection = {'path': args.source_connection}
    elif args.source_type == 'bigquery':
        source_connection = {
            'project_id': args.source_project,
            'dataset_id': args.source_database
        }
    else:
        # PostgreSQL or MySQL source
        source_connection = {
            'host': args.source_host,
            'port': args.source_port,
            'user': args.source_user,
            'password': args.source_password,
            'database': args.source_database
        }
    
    # Set up destination connection
    # Set up destination connection
    if args.target_type == 'spanner':
        dest_connection = {
            'project_id': args.target_project,
            'instance_id': args.target_instance,
            'database_id': args.target_database
        }
    elif args.target_type == 'bigquery':
        dest_connection = {
            'project_id': args.target_project,
            'dataset_id': args.target_database
        }
    else:
        dest_connection = {
            'host': args.target_host,
            'port': args.target_port,
            'user': args.target_user,
            'password': args.target_password,
            'database': args.target_database
        }
    
    orchestrator = QueryConversionOrchestrator(
        converter=converter,
        source_connection=source_connection,
        dest_connection=dest_connection,
        source_schema=source_schema,
        dest_schema=target_schema,
        max_attempts=args.max_attempts,
        num_workers=args.num_workers
    )
    
    # Convert queries
    output_file = Path(args.output) if args.output else None
    results = orchestrator.convert_queries(queries, output_file)
    
    # Return exit code based on results
    from db_convertor.query_converters.base import ConversionStatus
    all_matched = all(r.status == ConversionStatus.RESULT_MATCHED for r in results.values())
    
    return 0 if all_matched else 1


if __name__ == '__main__':
    sys.exit(main())

