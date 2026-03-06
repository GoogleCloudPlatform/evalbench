#!/bin/bash
# Archive old files from pre-refactor version

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
WORK_DIR="$(dirname "$SCRIPT_DIR")"
ARCHIVE_DIR="${WORK_DIR}/old_files_archive"

cd "${WORK_DIR}"

echo "Archiving old files to ${ARCHIVE_DIR}..."
mkdir -p "${ARCHIVE_DIR}"

# Archive old Python scripts (now replaced by src/)
if [ -f "agent.py" ]; then
    mv agent.py "${ARCHIVE_DIR}/"
    echo "  ✓ Archived agent.py"
fi

if [ -f "pipeline.py" ]; then
    mv pipeline.py "${ARCHIVE_DIR}/"
    echo "  ✓ Archived pipeline.py"
fi

if [ -f "orchestrator.py" ]; then
    mv orchestrator.py "${ARCHIVE_DIR}/"
    echo "  ✓ Archived orchestrator.py"
fi

if [ -f "sqlite_export.py" ]; then
    mv sqlite_export.py "${ARCHIVE_DIR}/"
    echo "  ✓ Archived sqlite_export.py"
fi

if [ -f "utils.py" ]; then
    mv utils.py "${ARCHIVE_DIR}/"
    echo "  ✓ Archived utils.py"
fi

# Archive old wrapper script (now in scripts/)
if [ -f "convert_db.sh" ]; then
    mv convert_db.sh "${ARCHIVE_DIR}/"
    echo "  ✓ Archived convert_db.sh"
fi

# Archive generated artifacts (now in migrations/)
if [ -f "pg_schema.sql" ]; then
    mv pg_schema.sql "${ARCHIVE_DIR}/"
    echo "  ✓ Archived pg_schema.sql"
fi

if [ -f "csv_convertor.py" ]; then
    mv csv_convertor.py "${ARCHIVE_DIR}/"
    echo "  ✓ Archived csv_convertor.py"
fi

# Archive old export directories
if [ -d "sqlite_export" ]; then
    mv sqlite_export "${ARCHIVE_DIR}/"
    echo "  ✓ Archived sqlite_export/"
fi

if [ -d "converted_csv" ]; then
    mv converted_csv "${ARCHIVE_DIR}/"
    echo "  ✓ Archived converted_csv/"
fi

if [ -d "exported_output" ]; then
    mv exported_output "${ARCHIVE_DIR}/"
    echo "  ✓ Archived exported_output/"
fi

# Archive old temp directories
if [ -d "temp_export" ]; then
    mv temp_export "${ARCHIVE_DIR}/"
    echo "  ✓ Archived temp_export/"
fi

# Archive old log files
if [ -f "orchestrator_run.log" ]; then
    mv orchestrator_run.log "${ARCHIVE_DIR}/"
    echo "  ✓ Archived orchestrator_run.log"
fi

if [ -f "orchestrator_run2.log" ]; then
    mv orchestrator_run2.log "${ARCHIVE_DIR}/"
    echo "  ✓ Archived orchestrator_run2.log"
fi

if [ -f "pipeline_error.txt" ]; then
    mv pipeline_error.txt "${ARCHIVE_DIR}/"
    echo "  ✓ Archived pipeline_error.txt"
fi

# Archive old documentation (keep for reference)
if [ -f "README.md" ]; then
    cp README.md "${ARCHIVE_DIR}/README_OLD.md"
    echo "  ✓ Copied README.md to archive"
fi

if [ -f "STATUS.md" ]; then
    mv STATUS.md "${ARCHIVE_DIR}/"
    echo "  ✓ Archived STATUS.md"
fi

if [ -f "SUMMARY.md" ]; then
    mv SUMMARY.md "${ARCHIVE_DIR}/"
    echo "  ✓ Archived SUMMARY.md"
fi

# Update main README
if [ -f "README_NEW.md" ]; then
    mv README.md "${ARCHIVE_DIR}/README_ORIGINAL.md"
    mv README_NEW.md README.md
    echo "  ✓ Updated main README.md"
fi

echo ""
echo "Cleanup complete!"
echo "Old files archived to: ${ARCHIVE_DIR}"
echo ""
echo "To restore old files: mv ${ARCHIVE_DIR}/* ${WORK_DIR}/"

