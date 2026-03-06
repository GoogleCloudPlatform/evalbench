#!/bin/bash
# Complete migration: Data + Query Conversion
# Usage: ./scripts/migrate_with_queries.sh --source-connection <db> --queries-file <csv> [options]

set -e

# Parse arguments
SOURCE_CONNECTION=""
QUERIES_FILE=""
DEST_HOST=""
DEST_PORT="5432"
DEST_USER=""
DEST_PASSWORD=""
DEST_DATABASE=""
MAX_DATA_ATTEMPTS=10
MAX_QUERY_ATTEMPTS=3
NUM_WORKERS=1

while [[ $# -gt 0 ]]; do
    case $1 in
        --source-connection) SOURCE_CONNECTION="$2"; shift 2 ;;
        --queries-file) QUERIES_FILE="$2"; shift 2 ;;
        --dest-host) DEST_HOST="$2"; shift 2 ;;
        --dest-port) DEST_PORT="$2"; shift 2 ;;
        --dest-user) DEST_USER="$2"; shift 2 ;;
        --dest-password) DEST_PASSWORD="$2"; shift 2 ;;
        --dest-database) DEST_DATABASE="$2"; shift 2 ;;
        --max-data-attempts) MAX_DATA_ATTEMPTS="$2"; shift 2 ;;
        --max-query-attempts) MAX_QUERY_ATTEMPTS="$2"; shift 2 ;;
        --num-workers) NUM_WORKERS="$2"; shift 2 ;;
        -h|--help)
            echo "Complete Database + Query Migration"
            echo ""
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Required:"
            echo "  --source-connection PATH    Source database path"
            echo "  --queries-file CSV          Queries CSV file"
            echo "  --dest-host HOST           Destination host"
            echo "  --dest-user USER           Destination user"
            echo "  --dest-password PASS       Destination password"
            echo "  --dest-database DB         Destination database"
            echo ""
            echo "Optional:"
            echo "  --dest-port PORT           Destination port (default: 5432)"
            echo "  --max-data-attempts N      Max attempts for data migration (default: 10)"
            echo "  --max-query-attempts N     Max attempts per query (default: 3)"
            echo "  --num-workers N            Number of parallel workers for queries (default: 1)"
            echo ""
            echo "Example:"
            echo "  $0 \\"
            echo "    --source-connection /path/to/database.sqlite \\"
            echo "    --queries-file queries.csv \\"
            echo "    --dest-host 136.119.143.89 \\"
            echo "    --dest-user postgres \\"
            echo "    --dest-password 'Admin@1234' \\"
            echo "    --dest-database mydb"
            exit 0
            ;;
        *) echo "Unknown option: $1"; exit 1 ;;
    esac
done

# Validate required arguments
if [ -z "$SOURCE_CONNECTION" ] || [ -z "$QUERIES_FILE" ] || [ -z "$DEST_HOST" ] || \
   [ -z "$DEST_USER" ] || [ -z "$DEST_PASSWORD" ] || [ -z "$DEST_DATABASE" ]; then
    echo "Error: Missing required arguments"
    echo "Use --help for usage information"
    exit 1
fi

if [ ! -f "$SOURCE_CONNECTION" ]; then
    echo "Error: Source database not found: $SOURCE_CONNECTION"
    exit 1
fi

if [ ! -f "$QUERIES_FILE" ]; then
    echo "Error: Queries file not found: $QUERIES_FILE"
    exit 1
fi

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WORK_DIR="$(dirname "$SCRIPT_DIR")"

echo "============================================="
echo "Complete Migration: Data + Queries"
echo "============================================="
echo "Source: $SOURCE_CONNECTION"
echo "Queries: $QUERIES_FILE"
echo "Destination: $DEST_USER@$DEST_HOST:$DEST_PORT/$DEST_DATABASE"
echo "============================================="
echo ""

# Step 1: Data Migration
echo "STEP 1: Data Migration"
echo "---------------------------------------------"
"${SCRIPT_DIR}/convert_db.sh" \
    --source-connection "$SOURCE_CONNECTION" \
    --dest-host "$DEST_HOST" \
    --dest-port "$DEST_PORT" \
    --dest-user "$DEST_USER" \
    --dest-password "$DEST_PASSWORD" \
    --dest-database "$DEST_DATABASE" \
    --max-attempts "$MAX_DATA_ATTEMPTS"

# Find the latest migration directory
MIGRATION_DIR=$(ls -td "${WORK_DIR}"/migrations/sqlite_to_postgresql_*_$(date +%Y%m%d)* 2>/dev/null | head -1)

if [ -z "$MIGRATION_DIR" ]; then
    echo "Error: No migration directory found"
    exit 1
fi

echo ""
echo "Migration directory: $MIGRATION_DIR"
echo ""

# Step 2: Query Conversion
echo "STEP 2: Query Conversion"
echo "---------------------------------------------"
python3 "${SCRIPT_DIR}/convert_queries.py" \
    --source-type sqlite \
    --target-type postgresql \
    --source-connection "$SOURCE_CONNECTION" \
    --source-schema "${MIGRATION_DIR}/source/schema.sql" \
    --target-schema "${MIGRATION_DIR}/artifacts/postgresql_schema.sql" \
    --queries-file "$QUERIES_FILE" \
    --target-host "$DEST_HOST" \
    --target-port "$DEST_PORT" \
    --target-user "$DEST_USER" \
    --target-password "$DEST_PASSWORD" \
    --target-database "$DEST_DATABASE" \
    --max-attempts "$MAX_QUERY_ATTEMPTS" \
    --num-workers "$NUM_WORKERS" \
    --output "${MIGRATION_DIR}/query_conversion_results.csv"

echo ""
echo "============================================="
echo "✅ Complete Migration Finished!"
echo "============================================="
echo "Migration directory: $MIGRATION_DIR"
echo ""
echo "Results:"
echo "  - Data artifacts: ${MIGRATION_DIR}/artifacts/"
echo "  - Query results:  ${MIGRATION_DIR}/query_conversion_results.csv"
echo ""
echo "Review query results:"
echo "  head -20 ${MIGRATION_DIR}/query_conversion_results.csv"
echo "============================================="

