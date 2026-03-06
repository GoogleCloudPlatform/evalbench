#!/bin/bash
# Convenience wrapper for database conversion

set -e

# Default values
SOURCE_CONNECTION=""
SOURCE_DIALECT="sqlite"
DEST_HOST=""
DEST_PORT="5432"
DEST_USER=""
DEST_PASSWORD=""
DEST_DATABASE=""
DEST_DIALECT="postgresql"
DB_NAME=""
MAX_ATTEMPTS=10
WORK_DIR=""

# Parse arguments
while [[ $# -gt 0 ]]; do
    case $1 in
        --source-connection)
            SOURCE_CONNECTION="$2"
            shift 2
            ;;
        --source-dialect)
            SOURCE_DIALECT="$2"
            shift 2
            ;;
        --dest-host)
            DEST_HOST="$2"
            shift 2
            ;;
        --dest-port)
            DEST_PORT="$2"
            shift 2
            ;;
        --dest-user)
            DEST_USER="$2"
            shift 2
            ;;
        --dest-password)
            DEST_PASSWORD="$2"
            shift 2
            ;;
        --dest-database)
            DEST_DATABASE="$2"
            shift 2
            ;;
        --dest-dialect)
            DEST_DIALECT="$2"
            shift 2
            ;;
        --db-name)
            DB_NAME="$2"
            shift 2
            ;;
        --max-attempts)
            MAX_ATTEMPTS="$2"
            shift 2
            ;;
        --work-dir)
            WORK_DIR="$2"
            shift 2
            ;;
        -h|--help)
            echo "Usage: $0 [OPTIONS]"
            echo ""
            echo "Required Options:"
            echo "  --source-connection PATH    Path to source database"
            echo "  --dest-host HOST           Destination database host"
            echo "  --dest-user USER           Destination database user"
            echo "  --dest-password PASS       Destination database password"
            echo "  --dest-database DB         Destination database name"
            echo ""
            echo "Optional:"
            echo "  --source-dialect TYPE      Source database type (default: sqlite)"
            echo "  --dest-dialect TYPE        Destination database type (default: postgresql)"
            echo "  --dest-port PORT           Destination port (default: 5432)"
            echo "  --db-name NAME             Database name for migration dir (auto-detected if omitted)"
            echo "  --max-attempts N           Maximum conversion attempts (default: 10)"
            echo "  --work-dir DIR             Working directory (default: script parent dir)"
            echo ""
            echo "Example:"
            echo "  $0 \\"
            echo "    --source-connection /path/to/database.sqlite \\"
            echo "    --dest-host 136.119.143.89 \\"
            echo "    --dest-user postgres \\"
            echo "    --dest-password 'Admin@1234' \\"
            echo "    --dest-database mydb \\"
            echo "    --max-attempts 10"
            exit 0
            ;;
        *)
            echo "Unknown option: $1"
            echo "Use --help for usage information"
            exit 1
            ;;
    esac
done

# Validate required arguments
if [ -z "$SOURCE_CONNECTION" ]; then
    echo "Error: --source-connection is required"
    echo "Use --help for usage information"
    exit 1
fi

if [ -z "$DEST_HOST" ]; then
    echo "Error: --dest-host is required"
    echo "Use --help for usage information"
    exit 1
fi

if [ -z "$DEST_USER" ]; then
    echo "Error: --dest-user is required"
    echo "Use --help for usage information"
    exit 1
fi

if [ -z "$DEST_PASSWORD" ]; then
    echo "Error: --dest-password is required"
    echo "Use --help for usage information"
    exit 1
fi

if [ -z "$DEST_DATABASE" ]; then
    echo "Error: --dest-database is required"
    echo "Use --help for usage information"
    exit 1
fi

# Set default work directory if not provided
SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
if [ -z "$WORK_DIR" ]; then
    WORK_DIR="$(dirname "$SCRIPT_DIR")"
fi

CLI_SCRIPT="${SCRIPT_DIR}/convert_database.py"

echo "=================================="
echo "Database Conversion"
echo "=================================="
echo "Source: ${SOURCE_CONNECTION} (${SOURCE_DIALECT})"
echo "Destination: ${DEST_USER}@${DEST_HOST}:${DEST_PORT}/${DEST_DATABASE} (${DEST_DIALECT})"
echo "Work dir: ${WORK_DIR}"
echo "Max attempts: ${MAX_ATTEMPTS}"
if [ -n "$DB_NAME" ]; then
    echo "Database name: ${DB_NAME}"
fi
echo "=================================="

# Run full conversion (export + convert integrated)
echo ""
echo "Running AI-powered conversion..."
echo "Flow: Source Export → AI Generation → Pipeline Execution"
echo ""

CMD="python3 ${CLI_SCRIPT} convert \
    --source-type ${SOURCE_DIALECT} \
    --target-type ${DEST_DIALECT} \
    --source-connection ${SOURCE_CONNECTION} \
    --target-host ${DEST_HOST} \
    --target-port ${DEST_PORT} \
    --target-user ${DEST_USER} \
    --target-password ${DEST_PASSWORD} \
    --target-database ${DEST_DATABASE} \
    --work-dir ${WORK_DIR} \
    --max-attempts ${MAX_ATTEMPTS}"

if [ -n "$DB_NAME" ]; then
    CMD="${CMD} --database-name ${DB_NAME}"
fi

eval $CMD

echo ""
echo "=================================="
echo "Conversion complete!"
echo "=================================="

