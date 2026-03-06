"""Pipeline for executing database conversion."""

import subprocess
import os
from pathlib import Path
from typing import Optional


class ConversionPipeline:
    """Pipeline for testing and executing database conversions."""
    
    def __init__(self, converter, importer):
        """Initialize pipeline.
        
        Args:
            converter: DatabaseConverter instance
            importer: DatabaseImporter instance
        """
        self.converter = converter
        self.importer = importer
    
    def run(
        self,
        schema_file: Path,
        convertor_script: Path,
        source_csv_dir: Path,
        converted_csv_dir: Path
    ) -> Optional[str]:
        """Run the conversion pipeline.
        
        Args:
            schema_file: Path to target schema SQL file
            convertor_script: Path to data convertor Python script
            source_csv_dir: Directory with source CSV files
            converted_csv_dir: Directory for converted CSV files
            
        Returns:
            Error message if pipeline fails, None if successful
        """
        try:
            # Create output directory
            os.makedirs(converted_csv_dir, exist_ok=True)

            # Check if any table-level checkpoints exist from a previous attempt.
            # If so, skip wipe+schema+csv-conversion and go straight to resuming import.
            checkpoint_files = list(Path(converted_csv_dir).glob('.imported_*'))
            resuming = len(checkpoint_files) > 0

            if resuming:
                print("\n" + "=" * 80)
                print(f"RESUMING import: {len(checkpoint_files)} table(s) already done, skipping wipe/schema/csv-convert")
                print("=" * 80)
            else:
                # Step 1: Wipe database
                self.importer.wipe_database()

                # Step 2: Create schema
                self.importer.create_schema(schema_file)

                # Step 3: Convert CSV files
                self._run_csv_convertor(convertor_script, source_csv_dir, converted_csv_dir)

            # Step 4: Upload CSV files (checkpoint-aware: skips completed tables)
            # Pass resuming=True only when we're resuming an interrupted import
            # (same artifacts/CSV files). When resuming=False, load_data will
            # truncate any partial table data and restart from row 0 to avoid
            # mixing data from different conversion attempts.
            tables = self.importer.get_table_dependencies()
            self.importer.load_csv_data(converted_csv_dir, tables, resuming=resuming)
            
            print("\n" + "=" * 80)
            print("✓ PIPELINE COMPLETED SUCCESSFULLY!")
            print("=" * 80)
            return None
            
        except Exception as e:
            print("\n" + "=" * 80)
            print("✗ PIPELINE FAILED!")
            print("=" * 80)
            print(str(e))
            return str(e)
    
    def _run_csv_convertor(self, convertor_script: Path, source_csv_dir: Path, output_csv_dir: Path):
        """Run the CSV convertor script.
        
        Args:
            convertor_script: Path to convertor script
            source_csv_dir: Source CSV directory
            output_csv_dir: Output CSV directory
        """
        print("\n" + "=" * 80)
        print("STEP: Converting CSV files")
        print("=" * 80)
        
        # Make sure the script is executable
        os.chmod(convertor_script, 0o755)
        
        cmd = f"python3 {convertor_script} {source_csv_dir} {output_csv_dir}"
        print(f"Running: {cmd}")
        
        result = subprocess.run(
            cmd,
            shell=True,
            capture_output=True,
            text=True
        )
        
        if result.returncode != 0:
            error_msg = f"CSV convertor failed with exit code {result.returncode}\n"
            error_msg += f"Command: {cmd}\n"
            if result.stdout:
                error_msg += f"STDOUT:\n{result.stdout}\n"
            if result.stderr:
                error_msg += f"STDERR:\n{result.stderr}\n"
            raise Exception(error_msg)
        
        print("✓ CSV files converted successfully")
        if result.stdout:
            print(result.stdout)

