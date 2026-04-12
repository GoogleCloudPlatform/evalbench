"""Orchestrator for managing the conversion process."""

import os
import json
from pathlib import Path
from datetime import datetime
from typing import Optional
from .agent import ConversionAgent
from .pipeline import ConversionPipeline


class ConversionOrchestrator:
    """Orchestrates the database conversion process with iterative refinement."""
    
    def __init__(self, converter):
        """Initialize orchestrator.
        
        Args:
            converter: DatabaseConverter instance
        """
        self.converter = converter
        self.config = converter.config
        self.agent = ConversionAgent(converter)
        self.importer = converter.get_importer()
        self.pipeline = ConversionPipeline(converter, self.importer)
    
    def run_conversion(
        self,
        export_source: bool = True,
        migration_dir: Optional[Path] = None
    ) -> bool:
        """Run the full conversion process with iterative refinement.
        
        Full flow:
        1. Export source database (if export_source=True)
        2. Generate conversion artifacts using AI
        3. Execute pipeline (create schema + convert data)
        4. Iterate until success or max attempts
        
        Args:
            export_source: Whether to export source database (default: True)
            migration_dir: Directory to store migration artifacts (auto-generated if None)
            
        Returns:
            True if successful, False otherwise
        """
        # Set up migration directory
        if migration_dir is None:
            migration_name = self.config.get_migration_name()
            migration_dir = self.config.work_dir / 'migrations' / migration_name
        
        migration_dir = Path(migration_dir)
        migration_dir.mkdir(parents=True, exist_ok=True)

        # Set up subdirectories
        artifacts_dir = migration_dir / 'artifacts'
        artifacts_dir.mkdir(exist_ok=True)

        converted_csv_dir = migration_dir / 'converted'
        converted_csv_dir.mkdir(exist_ok=True)

        logs_dir = migration_dir / 'logs'
        logs_dir.mkdir(exist_ok=True)

        source_dir = migration_dir / 'source'
        source_dir.mkdir(exist_ok=True)

        # Streaming fast path: bypass AI agent and CSV pipeline entirely
        if self.config.streaming:
            if self.config.source_type == 'postgresql' and self.config.target_type == 'mysql':
                return self._run_streaming_conversion(migration_dir)
            else:
                print(f"Error: --streaming is only supported for postgresql → mysql "
                      f"(got {self.config.source_type} → {self.config.target_type})")
                return False

        # STEP 1: Export source database
        if export_source:
            print("\n" + "=" * 80)
            print("STEP 1: EXPORTING SOURCE DATABASE")
            print("=" * 80)
            print(f"Database: {self.config.source_connection}")
            print(f"Type: {self.config.source_type}")
            print(f"Output: {source_dir}")
            
            try:
                exporter = self.converter.get_exporter()
                result = exporter.export_all(source_dir)
                
                print(f"\n✓ Export completed!")
                print(f"  Schema: {result['schema_path']}")
                print(f"  Tables: {result['table_count']}")
                print(f"  Total rows: {result['total_rows']}")
                
                if hasattr(exporter, 'close'):
                    exporter.close()
                    
            except Exception as e:
                print(f"\n✗ Export failed: {e}")
                import traceback
                traceback.print_exc()
                return False
        else:
            print("\n" + "=" * 80)
            print("STEP 1: USING EXISTING SOURCE DATA")
            print("=" * 80)
            print(f"Source: {source_dir}")
            
            # Verify source files exist
            if not (source_dir / 'schema.sql').exists():
                print(f"✗ Error: schema.sql not found in {source_dir}")
                return False
        
        # Save configuration
        config_path = migration_dir / 'config.json'
        with open(config_path, 'w') as f:
            json.dump({
                'source_type': self.config.source_type,
                'target_type': self.config.target_type,
                'database_name': self.config.database_name,
                'source_connection': self.config.source_connection,
                'timestamp': datetime.now().isoformat(),
                'max_attempts': self.config.max_attempts,
            }, f, indent=2)
        
        print("\n" + "=" * 80)
        print(f"{self.config.source_type.upper()} → {self.config.target_type.upper()} Migration")
        print("=" * 80)
        print(f"Database: {self.config.database_name}")
        print(f"Source: {self.config.source_connection}")
        print(f"Migration dir: {migration_dir}")
        print(f"Max attempts: {self.config.max_attempts}")
        print("=" * 80)
        
        # Orchestration loop
        pipeline_error = None
        prev_schema = None
        prev_convertor = None
        error_file = logs_dir / 'pipeline_error.txt'
        
        for attempt in range(1, self.config.max_attempts + 1):
            print(f"\n{'=' * 80}")
            print(f"ATTEMPT {attempt}/{self.config.max_attempts}")
            print(f"{'=' * 80}\n")
            
            # STEP 2: Run agent to generate artifacts
            try:
                print("STEP 2: AI AGENT - GENERATING CONVERSION ARTIFACTS")
                print("=" * 80)
                target_schema, data_convertor = self.agent.generate_conversion_artifacts(
                    source_dir / 'schema.sql',
                    source_dir,
                    prev_schema,
                    prev_convertor,
                    pipeline_error
                )
                
                # Save outputs to artifacts directory
                schema_filename = self.converter.get_schema_filename()
                convertor_filename = self.converter.get_convertor_filename()
                
                schema_path = artifacts_dir / schema_filename
                convertor_path = artifacts_dir / convertor_filename
                
                with open(schema_path, 'w', encoding='utf-8') as f:
                    f.write(target_schema)
                print(f"✓ Saved {schema_filename} ({len(target_schema)} chars)")
                
                # Patch: ensure all open() calls for reading CSV files use newline=''
                # This is required for csv.reader to correctly handle embedded newlines
                # in quoted fields (Python docs explicitly require this).
                import re as _re
                patched = data_convertor
                # Replace open(..., 'r', encoding='utf-8') and open(..., 'r') patterns
                # that don't already have newline='' to add newline=''
                patched = _re.sub(
                    r"open\(([^)]+),\s*['\"]r['\"]([^)]*)\)",
                    lambda m: (
                        m.group(0) if 'newline' in m.group(0)
                        else m.group(0).rstrip(')') + ", newline='')"
                    ),
                    patched
                )
                with open(convertor_path, 'w', encoding='utf-8') as f:
                    f.write(patched)
                os.chmod(convertor_path, 0o755)
                print(f"✓ Saved {convertor_filename} ({len(patched)} chars)")
                
                # Update previous paths for next iteration
                prev_schema = schema_path
                prev_convertor = convertor_path
                
                # Save attempt log
                attempt_log = logs_dir / f'attempt_{attempt}.log'
                with open(attempt_log, 'w') as f:
                    f.write(f"Attempt {attempt}\n")
                    f.write(f"Schema size: {len(target_schema)}\n")
                    f.write(f"Convertor size: {len(data_convertor)}\n")
                
            except Exception as e:
                print(f"✗ Agent failed: {e}")
                import traceback
                traceback.print_exc()
                continue
            
            # STEP 3: Run pipeline
            print("\nSTEP 3: PIPELINE - EXECUTING CONVERSION")
            print("=" * 80)
            error = self.pipeline.run(
                schema_path,
                convertor_path,
                source_dir,
                converted_csv_dir
            )
            
            if error is None:
                print("\n" + "=" * 80)
                print("🎉 SUCCESS! Migration completed successfully!")
                print("=" * 80)
                print(f"\nMigration artifacts saved to:")
                print(f"  {migration_dir}")
                print(f"\nFinal artifacts:")
                print(f"  - Target schema: {schema_path}")
                print(f"  - Data convertor: {convertor_path}")
                print(f"  - Converted CSVs: {converted_csv_dir}")
                print(f"  - Configuration: {config_path}")
                
                # Save success marker
                success_file = migration_dir / 'SUCCESS'
                with open(success_file, 'w') as f:
                    f.write(f"Migration completed successfully on attempt {attempt}\n")
                    f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                
                return True
            else:
                print(f"\n✗ Pipeline failed on attempt {attempt}")
                pipeline_error = error
                
                # Save error to file
                with open(error_file, 'w', encoding='utf-8') as f:
                    f.write(error)
                print(f"Error saved to {error_file}")
                
                if attempt < self.config.max_attempts:
                    print(f"\n🔄 Will retry with error feedback...")
                else:
                    print(f"\n✗ Max attempts ({self.config.max_attempts}) reached. Giving up.")
                    
                    # Save failure marker
                    failure_file = migration_dir / 'FAILED'
                    with open(failure_file, 'w') as f:
                        f.write(f"Migration failed after {self.config.max_attempts} attempts\n")
                        f.write(f"Timestamp: {datetime.now().isoformat()}\n")
                        f.write(f"Last error:\n{error}\n")
        
        return False
    
    def _run_streaming_conversion(self, migration_dir: Path) -> bool:
        """Stream data directly from PG to MySQL, bypassing AI agent and CSV pipeline.

        Only valid when source_type='postgresql' and target_type='mysql'.
        Uses psycopg2 named server-side cursors so memory usage is O(batch_size).
        Schema is generated deterministically from PG information_schema (no AI).
        """
        from ..converters.pg_to_mysql_streaming import PGToMySQLStreaming

        src = self.config.source_connection
        tgt = self.config.target_connection

        pg_cfg = dict(
            host=src['host'],
            port=str(src.get('port', '5432')),
            user=src['user'],
            password=src['password'],
        )
        mysql_cfg = dict(
            host=tgt['host'],
            port=int(tgt.get('port', 3306)),
            user=tgt['user'],
            password=tgt['password'],
        )

        print("\n" + "=" * 80)
        print("STREAMING MODE: PostgreSQL → MySQL (no AI, no CSV)")
        print("=" * 80)
        print(f"Database : {self.config.database_name}")
        print(f"PG       : {pg_cfg['host']}:{pg_cfg['port']}")
        print(f"MySQL    : {mysql_cfg['host']}:{mysql_cfg['port']}")
        print(f"Workers  : {self.config.streaming_workers}")
        print(f"Batch    : {self.config.streaming_batch_size} rows")
        print(f"Dir      : {migration_dir}")
        print("=" * 80)

        try:
            streamer = PGToMySQLStreaming(pg=pg_cfg, mysql=mysql_cfg, migration_dir=migration_dir)
            results = streamer.run(
                database=self.config.database_name,
                batch_size=self.config.streaming_batch_size,
                workers=self.config.streaming_workers,
            )
            total = sum(results.values())
            print(f"\nSUCCESS — {total:,} rows migrated to MySQL")
            return True
        except Exception as e:
            print(f"\nFAILED: {e}")
            import traceback
            traceback.print_exc()
            return False

    def run_query_conversion(
        self,
        migration_dir: Path,
        queries_csv: Path,
        max_attempts: int = 3,
        num_workers: int = 1
    ) -> bool:
        """Run query conversion as part of the migration.
        
        Args:
            migration_dir: Path to migration directory with artifacts
            queries_csv: Path to CSV file with source queries
            max_attempts: Maximum attempts per query
            num_workers: Number of parallel workers (default: 1)
            
        Returns:
            True if all queries converted successfully
        """
        from ..query_converters.sqlite_to_pg import SQLiteToPGQueryConverter
        from ..query_conversion_orchestrator import QueryConversionOrchestrator
        
        print(f"\n\n{'='  * 80}")
        print(f"QUERY CONVERSION")
        print(f"{'=' * 80}")
        print(f"Migration: {migration_dir.name}")
        print(f"Queries: {queries_csv}")
        print(f"{'=' * 80}\n")
        
        # Load schemas from migration artifacts
        source_schema_path = migration_dir / 'source' / 'schema.sql'
        dest_schema_path = migration_dir / 'artifacts' / self.converter.get_schema_filename()
        
        if not source_schema_path.exists():
            print(f"✗ Source schema not found: {source_schema_path}")
            return False
        
        if not dest_schema_path.exists():
            print(f"✗ Destination schema not found: {dest_schema_path}")
            return False
        
        with open(source_schema_path) as f:
            source_schema = f.read()
        
        with open(dest_schema_path) as f:
            dest_schema = f.read()
        
        # Set up query converter
        if self.config.source_type == 'sqlite' and self.config.target_type == 'postgresql':
            query_converter = SQLiteToPGQueryConverter()
        else:
            print(f"✗ Query conversion from {self.config.source_type} to {self.config.target_type} not supported yet")
            return False
        
        # Set up orchestrator
        source_connection = {'path': self.config.source_connection}
        dest_connection = self.config.target_connection
        
        query_orch = QueryConversionOrchestrator(
            converter=query_converter,
            source_connection=source_connection,
            dest_connection=dest_connection,
            source_schema=source_schema,
            dest_schema=dest_schema,
            max_attempts=max_attempts,
            num_workers=num_workers
        )
        
        # Load queries from CSV
        queries = query_orch.load_queries_from_csv(queries_csv)
        print(f"Loaded {len(queries)} queries from CSV")
        
        # Output file in migration directory
        output_file = migration_dir / 'query_conversion_results.csv'
        
        # Run conversion
        results = query_orch.convert_queries(queries, output_file)
        
        # Check if all succeeded
        from ..query_converters.base import ConversionStatus
        all_matched = all(r.status == ConversionStatus.CONVERTED_MATCHED for r in results.values())
        
        if all_matched:
            print(f"\n✓ All queries converted successfully!")
        
        return all_matched

