#!/usr/bin/env python3
"""CLI for the PG → MySQL streaming workflow.

Streams data directly from PostgreSQL to MySQL without writing CSV files.
Memory usage stays low regardless of table size (uses server-side cursors).

Usage examples:

  # Migrate a single database with default connection settings:
  python3 stream_pg_to_mysql.py google_trends

  # Resume (schema already exists, some tables already imported):
  python3 stream_pg_to_mysql.py google_trends --skip-schema

  # Only specific tables:
  python3 stream_pg_to_mysql.py ethereum_blockchain --tables live_contracts,token_transfers

  # Parallel tables (each table gets its own connections):
  python3 stream_pg_to_mysql.py usda_nass_agriculture --workers 4

  # Full custom connection args:
  python3 stream_pg_to_mysql.py mydb \\
    --pg-host 10.0.0.1 --pg-user myuser --pg-password secret \\
    --mysql-host 127.0.0.1 --mysql-user admin --mysql-password secret \\
    --migration-dir /tmp/mydb_migration
"""

import argparse
import logging
import sys
import time
from datetime import datetime
from pathlib import Path

# Allow running directly without installing the package
sys.path.insert(0, str(Path(__file__).parent.parent / 'src'))

from db_convertor.converters.pg_to_mysql_streaming import PGToMySQLStreaming

# ---------------------------------------------------------------------------
# Default connection parameters (override via CLI flags)
# ---------------------------------------------------------------------------
DEFAULT_PG = dict(
    host='35.236.48.232',
    port='5432',
    user='hailongtest',
    password='Admin@1234',
)

DEFAULT_MYSQL = dict(
    host='127.0.0.1',
    port=3306,
    user='admin',
    password='nl2SQL@1',
)

DEFAULT_MIGRATION_BASE = Path('/home/hailongli/working-dir-pg-mysql-conv/migrations')


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description='Stream PostgreSQL database to MySQL (no CSV intermediates)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    p.add_argument('database', help='Source PG database name (same name used for MySQL target)')

    # PG connection
    p.add_argument('--pg-host', default=DEFAULT_PG['host'])
    p.add_argument('--pg-port', default=DEFAULT_PG['port'])
    p.add_argument('--pg-user', default=DEFAULT_PG['user'])
    p.add_argument('--pg-password', default=DEFAULT_PG['password'])

    # MySQL connection
    p.add_argument('--mysql-host', default=DEFAULT_MYSQL['host'])
    p.add_argument('--mysql-port', default=DEFAULT_MYSQL['port'], type=int)
    p.add_argument('--mysql-user', default=DEFAULT_MYSQL['user'])
    p.add_argument('--mysql-password', default=DEFAULT_MYSQL['password'])

    # Run options
    p.add_argument('--tables', help='Comma-separated list of tables to process (default: all)')
    p.add_argument('--workers', type=int, default=1,
                   help='Parallel table workers (default: 1). Each worker uses its own connections.')
    p.add_argument('--batch-size', type=int, default=1000,
                   help='Rows per INSERT batch (default: 1000). Reduce for tables with large rows.')
    p.add_argument('--skip-schema', action='store_true',
                   help='Skip CREATE DATABASE/TABLE — useful when resuming after schema exists.')
    p.add_argument('--migration-dir',
                   help='Directory for schema file + SUCCESS marker. '
                        'Auto-generated under migrations/ if not specified.')
    return p


def main():
    args = build_parser().parse_args()

    # Set up logging to stdout
    logging.basicConfig(
        level=logging.INFO,
        format='[%(asctime)s] %(message)s',
        datefmt='%H:%M:%S',
        stream=sys.stdout,
    )

    # Resolve migration dir
    if args.migration_dir:
        mdir = Path(args.migration_dir)
    else:
        ts = datetime.now().strftime('%Y%m%d_%H%M%S')
        mdir = DEFAULT_MIGRATION_BASE / f'postgresql_to_mysql_{args.database}_{ts}_streaming'

    mdir.mkdir(parents=True, exist_ok=True)
    log_path = mdir / 'stream.log'

    # Also log to file
    file_handler = logging.FileHandler(log_path)
    file_handler.setFormatter(logging.Formatter('[%(asctime)s] %(message)s', '%H:%M:%S'))
    logging.getLogger().addHandler(file_handler)

    logging.info(f"Migration dir: {mdir}")
    logging.info(f"Database: {args.database}")
    logging.info(f"Workers: {args.workers}, batch_size: {args.batch_size}")

    pg_cfg = dict(
        host=args.pg_host,
        port=args.pg_port,
        user=args.pg_user,
        password=args.pg_password,
    )
    mysql_cfg = dict(
        host=args.mysql_host,
        port=args.mysql_port,
        user=args.mysql_user,
        password=args.mysql_password,
    )

    tables = [t.strip() for t in args.tables.split(',')] if args.tables else None

    streamer = PGToMySQLStreaming(pg=pg_cfg, mysql=mysql_cfg, migration_dir=mdir)

    t0 = time.time()
    try:
        results = streamer.run(
            database=args.database,
            tables=tables,
            batch_size=args.batch_size,
            workers=args.workers,
            skip_schema=args.skip_schema,
        )
    except Exception as e:
        logging.error(f"FAILED: {e}", exc_info=True)
        sys.exit(1)

    elapsed = time.time() - t0
    total = sum(results.values())
    logging.info(f"\nSUCCESS — {total:,} rows in {elapsed:.0f}s")
    logging.info(f"Log: {log_path}")


if __name__ == '__main__':
    main()
