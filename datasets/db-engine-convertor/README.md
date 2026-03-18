# Database Conversion Toolkit

An AI-powered, best-effort database migration system supporting any-to-any database conversions with automatic schema translation, data migration, and query conversion.

> **⚠️ Important**: This is a **best-effort conversion tool** designed for development, testing, and research purposes. It is **NOT recommended for production data** that requires precise, error-free conversion. Always validate conversions thoroughly before using in any critical environment.

## Overview

This toolkit uses Google Gemini AI to automatically generate database-specific schemas and data conversion scripts, enabling migrations between different database systems with minimal manual configuration. The system learns from errors through iterative refinement and provides reproducible migration artifacts.

### Key Features

- **AI-Powered Conversion**: Leverages Gemini AI to understand schema differences and generate conversion code
- **Iterative Error Correction**: Automatically learns from errors and refines conversions (up to 10 attempts)
- **Query Conversion**: Translates SQL queries between different SQL dialects with result validation
- **Reproducible Migrations**: All artifacts saved for identical rebuilds without re-running AI
- **Extensible Architecture**: Designed for any-to-any database migrations
- **Comprehensive Examples**: 7 working examples demonstrating various migration paths

## Supported Databases

| Source Database | Target Database | Data Migration | Query Conversion | Status |
|----------------|-----------------|----------------|------------------|--------|
| SQLite | PostgreSQL | ✅ | ✅ | Fully Supported |
| SQLite | MySQL | ✅ | ✅ | Fully Supported |
| SQLite | Cloud Spanner | ✅ | ✅ | Fully Supported |
| PostgreSQL | MySQL | ✅ | ✅ | Fully Supported |
| PostgreSQL | Cloud Spanner | ✅ | ✅ | Fully Supported |
| PostgreSQL | BigQuery | ✅ | ✅ | Fully Supported |
| BigQuery | PostgreSQL | ✅ | ✅ | Fully Supported |

**Known Issue*: PostgreSQL→BigQuery has a dataset naming bug where the AI generates schemas with the source database name instead of the target dataset name. See `examples/README.md` for workaround.

## Quick Start

### Installation

```bash
# Install dependencies
cd db-engine-convertor
pip install -r requirements.txt

# Set up environment variables
cp .env.example .env
# Edit .env with your database credentials and API keys
```

### Running Examples

The toolkit includes 7 comprehensive examples demonstrating various migration paths:

```bash
# Run individual examples
python3 examples/run_conversion_examples.py --example 1  # SQLite → PostgreSQL
python3 examples/run_conversion_examples.py --example 2  # SQLite → MySQL
python3 examples/run_conversion_examples.py --example 3  # SQLite → Spanner
python3 examples/run_conversion_examples.py --example 4  # PostgreSQL → MySQL
python3 examples/run_conversion_examples.py --example 5  # PostgreSQL → Spanner
python3 examples/run_conversion_examples.py --example 6  # PostgreSQL → BigQuery
python3 examples/run_conversion_examples.py --example 7  # BigQuery → PostgreSQL

# Run all examples in sequence
python3 examples/run_conversion_examples.py --all
```

See [`examples/README.md`](examples/README.md) for detailed documentation on each example.

### Basic Usage

```bash
# Data migration only
python3 scripts/convert_database.py convert \
    --source-type sqlite \
    --target-type postgresql \
    --source-connection /path/to/database.sqlite \
    --target-host localhost \
    --target-port 5432 \
    --target-user postgres \
    --target-password password \
    --target-database mydb \
    --work-dir ./migrations

# Data migration + query conversion
bash scripts/migrate_with_queries.sh \
    sqlite /path/to/database.sqlite \
    postgresql mydb localhost 5432 postgres password \
    /path/to/queries.csv
```

## Architecture

### Design Concepts

#### 1. **Static Code vs. Generated Artifacts**

- **Static Code** (`src/db_convertor/`): Reusable, version-controlled Python components
  - Exporters: Extract schema and data from source databases
  - Importers: Load schema and data into target databases
  - Converters: Configuration and coordination for specific migration paths
  - AI Agent: Gemini-powered artifact generation
  - Pipeline: Execution engine with error handling

- **Generated Artifacts** (`migrations/*/artifacts/`): AI-generated, migration-specific files
  - `<target>_schema.sql`: Target database DDL statements
  - `data_convertor.py`: Python script to transform CSV data
  - These can be **saved and reused** for identical rebuilds
  - Ensures reproducibility without re-running expensive AI calls

#### 2. **Iterative Refinement Loop**

```
┌─────────────────────────────────────────────────┐
│  1. Export source database (schema + data)     │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│  2. AI generates target schema + convertor     │
│     (Gemini analyzes source schema)             │
└─────────────────┬───────────────────────────────┘
                  │
┌─────────────────▼───────────────────────────────┐
│  3. Execute pipeline:                           │
│     a. Create schema in target DB              │
│     b. Run data convertor on CSVs              │
│     c. Load converted data                      │
└─────────────────┬───────────────────────────────┘
                  │
              Success? ──No──> Package error details
                  │                     │
                 Yes                    ▼
                  │            Send to AI for refinement
                  │            (Back to step 2, max 10 times)
                  │
┌─────────────────▼───────────────────────────────┐
│  4. Save artifacts + mark SUCCESS               │
└─────────────────────────────────────────────────┘
```

#### 3. **Query Conversion with Validation**

- Converts SQL queries from source dialect to target dialect
- **Executes both versions** and compares results
- Iteratively refines queries that produce different results
- Classifies results: `result_matched`, `unable_to_match`, `exhausted_retry`

### Directory Structure

```
db-engine-convertor/
├── src/db_convertor/              # Core toolkit (static code)
│   ├── core/                      # Main orchestration
│   │   ├── agent.py              # AI agent for artifact generation
│   │   ├── pipeline.py           # Execution pipeline
│   │   └── orchestrator.py      # Conversion coordinator
│   ├── exporters/                 # Database exporters
│   │   ├── sqlite_exporter.py
│   │   ├── pg_exporter.py
│   │   └── bigquery_exporter.py
│   ├── importers/                 # Database importers
│   │   ├── pg_importer.py
│   │   ├── mysql_importer.py
│   │   ├── spanner_importer.py
│   │   └── bigquery_importer.py
│   ├── converters/                # Conversion configurations
│   │   ├── sqlite_to_pg.py
│   │   ├── pg_to_mysql.py
│   │   ├── pg_to_bigquery.py
│   │   └── ... (7 converters total)
│   ├── query_converters/          # Query dialect conversion
│   │   ├── sqlite_to_pg.py
│   │   ├── pg_to_mysql.py
│   │   └── ... (7 query converters total)
│   └── utils/
│       └── llm.py                # Gemini API wrapper
├── scripts/                       # CLI tools
│   ├── convert_database.py       # Data migration CLI
│   ├── convert_queries.py        # Query conversion CLI
│   └── migrate_with_queries.sh   # Combined wrapper script
├── examples/                      # Example migrations
│   ├── run_conversion_examples.py  # Unified example runner
│   ├── README.md                   # Examples documentation
│   ├── california_schools_queries.csv  # Sample queries (89 queries)
│   ├── 1_sqlite_to_pg/            # Example outputs
│   ├── 2_sqlite_to_mysql/
│   └── ... (7 example directories)
└── migrations/                    # User migration outputs
    └── <source>_to_<target>_<timestamp>/
        ├── config.json           # Migration configuration
        ├── source/               # Source database snapshot
        │   ├── schema.sql
        │   └── *.csv
        ├── artifacts/            # Generated artifacts (reusable!)
        │   ├── <target>_schema.sql
        │   └── data_convertor.py
        ├── converted/            # Transformed data
        │   └── *.csv
        ├── logs/                 # Execution logs
        └── SUCCESS               # Status marker
```

## Best Practices

### For Development and Testing

✅ **Good Use Cases:**
- Migrating development/test databases
- Prototyping schema changes
- Comparing database query performance across platforms
- Learning database dialect differences
- Generating initial migration scaffolding

### For Production

⚠️ **Exercise Caution:**
- This is a **best-effort tool** - AI-generated conversions may have subtle errors
- **Always validate** converted data thoroughly
- **Test queries extensively** against the converted database
- Consider using generated artifacts as a starting point, then manually refine
- Implement comprehensive testing before production use

### Recommended Workflow

1. **Run Examples First**: Familiarize yourself with the toolkit using provided examples
2. **Small Test Migration**: Start with a small subset of your data
3. **Manual Review**: Examine generated `artifacts/` files for correctness
4. **Validate Data**: Compare row counts, data types, constraints
5. **Test Queries**: Run your application queries and compare results
6. **Iterative Refinement**: If conversion fails, review error logs and retry
7. **Save Artifacts**: Once successful, save artifacts for reproducible rebuilds

### Environment Configuration

Create a `.env` file with your credentials (see `.env.example`):

```bash
# AI Configuration
GEMINI_API_KEY=your_api_key_here
GCP_PROJECT_ID=your_project_id

# Database Credentials
PG_HOST=localhost
PG_PORT=5432
PG_USER=postgres
PG_PASSWORD=your_password

MYSQL_HOST=localhost
MYSQL_PORT=3306
MYSQL_USER=root
MYSQL_PASSWORD=your_password

# Cloud Credentials
SPANNER_INSTANCE=your_instance
SPANNER_DB=your_database
BQ_DATASET=your_dataset
```

## Extending the Toolkit

To add support for a new database:

1. **Create Exporter** (`src/db_convertor/exporters/newdb_exporter.py`)
   - Extend `DatabaseExporter` base class
   - Implement schema extraction and data export

2. **Create Importer** (`src/db_convertor/importers/newdb_importer.py`)
   - Extend `DatabaseImporter` base class
   - Implement schema creation and data loading

3. **Create Converter** (`src/db_convertor/converters/source_to_newdb.py`)
   - Extend `DatabaseConverter` base class
   - Configure prompts for AI agent
   - Define schema and convertor filenames

4. **Create Query Converter** (`src/db_convertor/query_converters/source_to_newdb.py`)
   - Extend `BaseQueryConverter` base class
   - Implement query execution logic

5. **Update Scripts**: Add new conversion type to `scripts/convert_database.py`

6. **Add Example**: Create example in `examples/` directory

## Query Conversion

The toolkit can convert queries between SQL dialects:

```bash
python3 scripts/convert_queries.py \
    --source-type postgresql \
    --target-type mysql \
    --source-connection "..." \
    --queries-file queries.csv \
    --target-host localhost \
    --target-database mydb \
    ...
```

### Query Conversion Process

1. **Load Queries**: Read queries from CSV (with question_id and query columns)
2. **Convert Dialect**: AI translates query to target SQL dialect
3. **Execute Both**: Run original query on source, converted query on target
4. **Compare Results**: Validate that results match (row count + data)
5. **Iterative Refinement**: If mismatch, send diff to AI for correction (max 5 attempts)
6. **Report**: Generate CSV with conversion status and any failures

### Expected Results

- **result_matched**: Query successfully converted and validated ✅
- **unable_to_match**: Results differ after max attempts ⚠️
- **exhausted_retry**: Conversion failed after max attempts ⚠️

It's normal to have some failures, especially for complex queries with dialect-specific features.

## Troubleshooting

### Common Issues

1. **API Key Errors**: Ensure `GEMINI_API_KEY` is set in `.env`
2. **Connection Failures**: Verify database credentials and network access
3. **Schema Errors**: Check target database permissions for DDL operations
4. **Data Type Mismatches**: Review `data_convertor.py` for type conversion logic
5. **Query Conversion Failures**: Some SQL features don't translate perfectly between dialects

### Getting Help

- Check `migrations/*/logs/` for detailed error messages
- Review generated `artifacts/` files for AI logic
- Consult `examples/README.md` for working examples
- Increase `--max-attempts` for complex conversions

## Performance Considerations

- **Large Databases**: Export/import can be slow for databases with millions of rows
- **AI Calls**: Each iteration calls Gemini API (costs apply)
- **Query Conversion**: Parallel workers (`--num-workers`) speed up large query sets
- **Reproducibility**: Reusing saved artifacts skips expensive AI generation
