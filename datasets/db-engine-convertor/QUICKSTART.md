# Quick Start Guide

## Overview

This toolkit converts databases between different engines using AI-powered schema and data transformation.

**Working Directory**: `db-engine-convertor`

## Installation

```bash
cd db-engine-convertor

# Set Gemini API key
export GEMINI_API_KEY="your-key-here"

# Make scripts executable (if not already)
chmod +x scripts/*.py scripts/*.sh
```

## Usage

### Option 1: Quick Conversion (Wrapper Script)

```bash
./scripts/convert_db.sh \
    --source-connection /path/to/database.sqlite \
    --dest-host pg_host \
    --dest-user pg_user \
    --dest-password pg_password \
    --dest-database pg_database \
    --max-attempts 10
```

**Optional flags:**
- `--source-dialect` (default: sqlite)
- `--dest-dialect` (default: postgresql)
- `--dest-port` (default: 5432)
- `--db-name` (auto-detected from filename if omitted)
- `--work-dir` (default: current directory)

**Example (California Schools):**
```bash
./scripts/convert_db.sh \
    --source-connection /home/username/bird_data/dev_20240627/dev_databases/california_schools/california_schools.sqlite \
    --dest-host 1.2.3.4 \
    --dest-user postgres \
    --dest-password 'password' \
    --dest-database california_schools \
    --max-attempts 10
```

**Get help:**
```bash
./scripts/convert_db.sh --help
```

### Option 2: Direct CLI (Full Control)

#### Full Conversion (Export integrated)

**SQLite → PostgreSQL:**
```bash
python3 scripts/convert_database.py convert \
    --source-type sqlite \
    --target-type postgresql \
    --source-connection /path/to/database.sqlite \
    --target-host 1.2.3.4 \
    --target-port 5432 \
    --target-user postgres \
    --target-password 'password' \
    --target-database mydb \
    --work-dir . \
    --max-attempts 10
```

**SQLite → MySQL:**
```bash
python3 scripts/convert_database.py convert \
    --source-type sqlite \
    --target-type mysql \
    --source-connection /path/to/database.sqlite \
    --target-host 136.114.180.162 \
    --target-port 3306 \
    --target-user dbuser \
    --target-password 'password' \
    --target-database mydb \
    --work-dir . \
    --max-attempts 10
```

**Note**: Export is now integrated! The CLI automatically exports the source database as part of the conversion flow. Target database will be created automatically if it doesn't exist.

#### Replay Migration (Reproducibility)

To rebuild database using saved artifacts:

```bash
python3 scripts/convert_database.py replay \
    migrations/sqlite_to_postgresql_california_schools_20241218_123456 \
    --target-host 1.2.3.4 \
    --target-port 5432 \
    --target-user postgres \
    --target-password 'password' \
    --target-database mydb
```

The migration directory name now includes the database name for better organization!

## What Happens

**Integrated Flow: Source → Dump → Artifacts → Destination**

1. **STEP 1 - Source Export**: Extracts schema.sql + CSV files from source database
2. **STEP 2 - AI Generation**: Gemini creates target schema + data conversion script
3. **STEP 3 - Pipeline Execution**:
   - Wipes target database
   - Creates new schema
   - Converts CSV data
   - Loads data in dependency order
4. **Iteration**: If errors occur, feeds them back to AI (repeats until success)

All steps are integrated into a single command!

## Output

Migrations are saved to timestamped directories **with database name**:

```
migrations/sqlite_to_postgresql_<database_name>_TIMESTAMP/
├── config.json              # Migration metadata
├── SUCCESS or FAILED        # Status marker
├── artifacts/              # 🎁 Reproducible packages
│   ├── postgresql_schema.sql
│   └── data_convertor.py
├── source/                 # 📸 Source snapshot (auto-exported)
│   ├── schema.sql
│   └── *.csv
├── converted/              # 🔄 Converted data
│   └── *.csv
└── logs/                   # 📋 Execution logs
```

Example: `migrations/sqlite_to_postgresql_california_schools_20241218_123456/`

## Reproducibility

The `artifacts/` directory contains everything needed to recreate the database identically:
- Target schema SQL
- Data conversion script

Replay using these artifacts ensures **identical** rebuilds (avoiding LLM non-determinism).

## Supported Conversions

| Source | Target | Status |
|--------|--------|--------|
| SQLite | PostgreSQL | ✅ Fully tested |
| Others | ... | 🚧 Easy to add |

## Example Results

**California Schools Database:**
- Source: 29,941 rows (3 tables)
- AI Attempts: 3
- Status: ✅ SUCCESS
- Time: ~3 minutes
- All foreign keys preserved
- 100% data integrity

## Troubleshooting

### Check Migration Logs

```bash
cat migrations/*/logs/pipeline_error.txt
cat migrations/*/logs/attempt_*.log
```

### Verify Database

```bash
PGPASSWORD=password psql -h host -p port -U user -d database -c "
  SELECT tablename, n_live_tup 
  FROM pg_stat_user_tables 
  ORDER BY tablename;
"
```

### Increase Attempts

If AI is making progress but hitting max attempts, increase:

```bash
--max-attempts 15
```

## Clean Up Old Files

After verifying the new system works:

```bash
./scripts/cleanup_old_files.sh
```

This archives old files to `old_files_archive/`.

## Directory Structure

```
db-engine-convertor/
├── src/db_convertor/       # Static code (versioned)
│   ├── core/               # Agent, Pipeline, Orchestrator
│   ├── exporters/          # Database exporters
│   ├── importers/          # Database importers
│   ├── converters/         # Conversion configs
│   └── utils/              # LLM utilities
├── migrations/             # Generated artifacts
├── scripts/                # CLI tools
└── [docs]/                 # Documentation
```

## Help

```bash
# Get help
python3 scripts/convert_database.py --help

# Export help
python3 scripts/convert_database.py export --help

# Convert help
python3 scripts/convert_database.py convert --help

# Replay help
python3 scripts/convert_database.py replay --help
```

## Key Features

✅ **AI-Powered**: Automatic schema translation
✅ **Iterative**: Learns from errors
✅ **Reproducible**: Save artifacts as packages
✅ **Extensible**: Easy to add new databases
✅ **Production-Ready**: Tested with real data
✅ **Type-Safe**: Handles complex type conversions
✅ **Dependency-Aware**: Loads tables in correct order

## Next Steps

- See `README.md` for full documentation
- See `REFACTORING_SUMMARY.md` for technical details
- See `REFACTORING_COMPLETE.md` for architecture

## Query Conversion (New!)

After converting your database, convert your queries too!

```bash
python3 scripts/convert_queries.py \
    --source-type sqlite \
    --target-type postgresql \
    --source-connection /path/to/database.sqlite \
    --source-schema migrations/*/source/schema.sql \
    --target-schema migrations/*/artifacts/postgresql_schema.sql \
    --queries-file queries.sql \
    --target-host 1.2.3.4 \
    --target-user postgres \
    --target-password 'password' \
    --target-database mydb \
    --output query_results.json
```

See `README.md` for full documentation and `examples/` for test scripts.

**Test with California Schools:**
```bash
./examples/test_query_conversion.sh
```

## Contact

For issues, check the logs in `migrations/*/logs/` for detailed error information.

