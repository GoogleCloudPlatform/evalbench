# Database Conversion Examples

Comprehensive examples demonstrating database conversion across different engines with query translation and validation.

## Quick Start

### 1. Setup Environment

Copy the example environment file and configure your credentials:

```bash
cp .env.example .env
# Edit .env with your database credentials and paths
```

Required configuration:
- **API Keys**: GEMINI_API_KEY, GCP_PROJECT_ID
- **Source Data**: SQLITE_DB_PATH, SQLITE_QUERIES_PATH
- **Database Credentials**: PostgreSQL, MySQL, BigQuery, Spanner

### 2. Run Examples

```bash
# Run a specific example
python3 examples/run_conversion_examples.py --example 1

# Run all examples in sequence
python3 examples/run_conversion_examples.py --all

# Get help
python3 examples/run_conversion_examples.py --help
```

## Available Examples

### Example 1: SQLite → PostgreSQL ✅
**Status**: Fully implemented

Converts the California Schools SQLite database to PostgreSQL with query conversion.

**What it does**:
1. Migrates data from SQLite to PostgreSQL
2. Converts 89 SQLite queries to PostgreSQL dialect
3. Validates query results match between databases

**Output**:
- `examples/1_sqlite_to_pg/migrations/<timestamp>/` - Migration artifacts
- `examples/1_sqlite_to_pg/pg_queries.csv` - Converted queries (used by Example 4)

**Run**:
```bash
python3 examples/run_conversion_examples.py --example 1
```

---

### Example 2: SQLite → MySQL ✅
**Status**: Fully implemented

Converts the California Schools SQLite database to MySQL with query conversion.

**What it does**:
1. Migrates data from SQLite to MySQL
2. Converts 89 SQLite queries to MySQL dialect
3. Validates query results match between databases

**Output**:
- `examples/2_sqlite_to_mysql/migrations/<timestamp>/` - Migration artifacts
- `examples/2_sqlite_to_mysql/mysql_queries.csv` - Converted queries

**Run**:
```bash
python3 examples/run_conversion_examples.py --example 2
```

---

### Example 3: SQLite → Spanner ✅
**Status**: Fully implemented

Converts the California Schools SQLite database to Cloud Spanner with query conversion.

**What it does**:
1. Migrates data from SQLite to Cloud Spanner
2. Converts 89 SQLite queries to Spanner SQL dialect
3. Validates query results match between databases

**Output**:
- `examples/3_sqlite_to_spanner/migrations/<timestamp>/` - Migration artifacts
- `examples/3_sqlite_to_spanner/spanner_queries.csv` - Converted queries

**Run**:
```bash
python3 examples/run_conversion_examples.py --example 3
```

---

### Example 4: PostgreSQL → MySQL ✅
**Status**: Fully implemented
**Prerequisites**: Example 1 must be completed first

Demonstrates multi-hop conversion by migrating from PostgreSQL (created in Example 1) to MySQL.

**What it does**:
1. Uses the PostgreSQL database from Example 1
2. Migrates data to a second MySQL database (`california_schools_from_pg`)
3. Converts the PostgreSQL queries (from Example 1) to MySQL dialect
4. Validates all query results match

**Output**:
- `examples/4_pg_to_mysql/migrations/<timestamp>/` - Migration artifacts
- `examples/4_pg_to_mysql/mysql_from_pg_queries.csv` - Converted queries

**Run**:
```bash
# Requires Example 1 to be completed first
python3 examples/run_conversion_examples.py --example 4
```

---

### Example 5: PostgreSQL → Spanner ✅
**Status**: Fully implemented
**Prerequisites**: Example 1

Migrates PostgreSQL database (from Example 1) to Cloud Spanner.

**What it does**:
1. Uses the PostgreSQL database from Example 1
2. Migrates data to Cloud Spanner
3. Converts the PostgreSQL queries (from Example 1) to Spanner SQL
4. Validates all query results match

**Output**:
- `examples/5_pg_to_spanner/migrations/<timestamp>/` - Migration artifacts
- `examples/5_pg_to_spanner/spanner_from_pg_queries.csv` - Converted queries

**Run**:
```bash
# Requires Example 1 to be completed first
python3 examples/run_conversion_examples.py --example 5
```

---

### Example 6: PostgreSQL → BigQuery ✅
**Status**: Fully implemented
**Prerequisites**: Example 1

Migrates PostgreSQL database (from Example 1) to BigQuery.

**What it does**:
1. Uses the PostgreSQL database from Example 1
2. Migrates data to BigQuery
3. Converts the PostgreSQL queries (from Example 1) to BigQuery SQL
4. Validates all query results match

**Output**:
- `examples/6_pg_to_bq/migrations/<timestamp>/` - Migration artifacts
- `examples/6_pg_to_bq/bq_queries.csv` - Converted queries (used by Example 7)

**Run**:
```bash
# Requires Example 1 to be completed first
python3 examples/run_conversion_examples.py --example 6
```

---

### Example 7: BigQuery → PostgreSQL ✅
**Status**: Fully implemented
**Prerequisites**: Example 6

Demonstrates round-trip conversion by migrating BigQuery data back to PostgreSQL.

**What it does**:
1. Uses the BigQuery dataset from Example 6
2. Migrates data to a second PostgreSQL database (`california_schools_from_bq`)
3. Converts the BigQuery queries (from Example 6) to PostgreSQL SQL
4. Validates all query results match

**Output**:
- `examples/7_bq_to_pg/migrations/<timestamp>/` - Migration artifacts
- `examples/7_bq_to_pg/pg_from_bq_queries.csv` - Converted queries

**Run**:
```bash
# Requires Example 6 to be completed first
python3 examples/run_conversion_examples.py --example 7
```

---

## Example Output Structure

Each example creates a directory with the following structure:

```
examples/N_source_to_target/
├── migrations/                          # All migration runs
│   └── <source>_to_<target>_<db>_<timestamp>/
│       ├── SUCCESS                      # Status marker
│       ├── config.json                  # Migration configuration
│       ├── artifacts/                   # Reproducible conversion artifacts
│       │   ├── <target>_schema.sql     # Generated target schema
│       │   └── data_convertor.py       # Data conversion script
│       ├── source/                      # Source database snapshot
│       │   ├── schema.sql
│       │   └── *.csv                   # Exported data
│       ├── converted/                   # Converted data
│       │   └── *.csv
│       └── logs/                        # Execution logs
│           ├── attempt_*.log
│           └── pipeline_error.txt
└── <target>_queries.csv                 # Converted queries (for dependent examples)
```

## Understanding Query Conversion

Each example includes query conversion that:
1. **Executes source query** on source database
2. **AI-converts the query** to target dialect (max 5 attempts)
3. **Executes converted query** on target database
4. **Compares results** (row count, column names, data values)
5. **Iterates** until results match or max attempts reached

**Result statuses**:
- `result_matched`: ✅ Query converted successfully, results match
- `unable_to_match`: ❌ Cannot convert due to schema/data differences
- `exhausted_retry`: ⚠️ Max attempts reached without match

## Replay Migrations

Each completed example prints a replay command that can rebuild the database using saved artifacts:

```bash
python3 scripts/convert_database.py replay \
    examples/1_sqlite_to_pg/migrations/<timestamp> \
    --target-host HOST \
    --target-user USER \
    --target-password PASS \
    --target-database DB
```

Replaying ensures **identical** rebuilds without re-running AI inference.

## Data Source

All examples use the **California Schools** database from the BIRD benchmark:
- **Database**: `/home/username/bird_data/dev_20240627/dev_databases/california_schools/california_schools.sqlite`
- **Queries**: `examples/california_schools_queries.csv` (89 queries)
- **Size**: 29,941 rows across 3 tables (schools, frpm, satscores)
- **Complexity**: Foreign keys, NULL handling, aggregate functions, joins

## Environment Variables Reference

```bash
# API Configuration
GEMINI_API_KEY=...              # Required for AI-powered conversion
GCP_PROJECT_ID=...              # Required for Vertex AI, BigQuery, Spanner

# Source Data
SQLITE_DB_PATH=...              # Path to california_schools.sqlite
SQLITE_QUERIES_PATH=...         # Path to queries CSV

# PostgreSQL
PG_HOST=...
PG_PORT=5432
PG_USER=...
PG_PASSWORD=...
PG_DB_1=california_schools                  # Example 1
PG_DB_2=california_schools_from_bq          # Example 7

# MySQL
MYSQL_HOST=...
MYSQL_PORT=3306
MYSQL_USER=...
MYSQL_PASSWORD=...
MYSQL_DB_1=california_schools               # Example 2
MYSQL_DB_2=california_schools_from_pg       # Example 4

# BigQuery
BQ_DATASET=california_schools_pg            # Example 6

# Spanner
SPANNER_INSTANCE=...
SPANNER_DB=california_schools
```

## Troubleshooting

### Missing .env file
```bash
cp .env.example .env
# Edit .env with your credentials
```

### Example fails prerequisite check
```bash
# Example 4 requires Example 1
python3 examples/run_conversion_examples.py --example 1
python3 examples/run_conversion_examples.py --example 4
```

### Database connection errors
- Verify credentials in `.env`
- Check network connectivity to database hosts
- Ensure databases exist (they will be auto-created if needed)

### Query conversion failures
This is normal! Some queries may not convert perfectly due to:
- Dialect differences (e.g., date functions)
- Unsupported features
- Schema differences

Check the output CSV for detailed conversion results.

## Next Steps

- See [../README.md](../README.md) for full toolkit documentation
- See [../QUICKSTART.md](../QUICKSTART.md) for quick start guide
- Check example output CSVs for query conversion results
- Review migration logs for detailed execution information
