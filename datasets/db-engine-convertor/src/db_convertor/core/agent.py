"""AI agent for database conversion."""

import json
import csv
import sys
csv.field_size_limit(sys.maxsize)
from pathlib import Path
from typing import Dict, Optional, Tuple
from ..utils.llm import gemini_inference


def get_csv_summary(csv_path: Path, num_lines=5, max_sample_rows=500,
                    max_cell_len=200) -> Dict:
    """Get summary of CSV file: first N and last N lines, column names, row count, max column lengths.
    
    Args:
        csv_path: Path to CSV file
        num_lines: Number of lines to include from start and end
        max_sample_rows: Max rows to read for max_lengths calculation (avoids OOM on huge files)
        max_cell_len: Truncate cell values to this length in sample rows (avoids token bloat
                      from large geometry/text fields in tables like us_cities_area)
        
    Returns:
        Dict with columns, total_rows, first_lines, last_lines, max_lengths
    """
    def _truncate_row(row):
        return [v[:max_cell_len] + '...' if len(v) > max_cell_len else v for v in row]

    first_rows = []
    sample_rows = []
    total_rows = 0
    count_limit = 100_000  # stop counting after this many rows to avoid full-scan of huge files

    with open(csv_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.reader(f)
        try:
            header = next(reader)
        except StopIteration:
            return {
                'columns': [],
                'total_rows': 0,
                'first_lines': [],
                'last_lines': [],
                'max_lengths': {}
            }

        max_lengths = {col_name: 0 for col_name in header}

        for row in reader:
            total_rows += 1
            
            # Update max lengths efficiently without holding all rows in memory
            for i, cell in enumerate(row):
                if i < len(header):
                    cl = len(str(cell))
                    if cl > max_lengths[header[i]]:
                        max_lengths[header[i]] = cl

            if total_rows <= num_lines:
                first_rows.append(_truncate_row(row))
            if total_rows <= max_sample_rows:
                sample_rows.append(row)
            if total_rows >= count_limit:
                # Stop counting; we'll report as "> count_limit"
                break

    exact_count = total_rows < count_limit
    reported_total = total_rows if exact_count else f">{count_limit}"

    # For last_lines: if file is small enough use sample, otherwise skip to save time
    if exact_count and total_rows <= max_sample_rows:
        last_lines = [_truncate_row(r) for r in sample_rows[-num_lines:]] if total_rows > num_lines else []
    else:
        last_lines = []  # skip for large files - first rows are enough for schema inference

    return {
        'columns': header,
        'total_rows': reported_total,
        'first_lines': first_rows,
        'last_lines': last_lines,
        'max_lengths': max_lengths
    }


class ConversionAgent:
    """AI agent for generating database conversion artifacts."""
    
    def __init__(self, converter):
        """Initialize agent.
        
        Args:
            converter: DatabaseConverter instance
        """
        self.converter = converter
    
    def get_csv_summaries(self, csv_dir: Path) -> Dict[str, Dict]:
        """Get summaries for all CSV files in directory.
        
        Args:
            csv_dir: Directory containing CSV files
            
        Returns:
            Dict mapping table names to CSV summaries
        """
        csv_summaries = {}
        csv_files = sorted(Path(csv_dir).glob('*.csv'))
        for csv_file in csv_files:
            table_name = csv_file.stem
            csv_summaries[table_name] = get_csv_summary(csv_file)
        return csv_summaries
    
    def generate_conversion_artifacts(
        self,
        source_schema_path: Path,
        source_csv_dir: Path,
        prev_schema_path: Optional[Path] = None,
        prev_convertor_path: Optional[Path] = None,
        pipeline_error: Optional[str] = None
    ) -> Tuple[str, str]:
        """Generate schema and data convertor using AI.
        
        Args:
            source_schema_path: Path to source database schema file
            source_csv_dir: Directory containing source CSV files
            prev_schema_path: Path to previous target schema (if any)
            prev_convertor_path: Path to previous convertor script (if any)
            pipeline_error: Error message from previous pipeline run (if any)
            
        Returns:
            Tuple of (target_schema, data_convertor_script)
        """
        # Read source schema
        with open(source_schema_path, 'r', encoding='utf-8') as f:
            source_schema = f.read()
        
        # Get CSV summaries
        csv_summaries = self.get_csv_summaries(source_csv_dir)
        
        # Build prompt using converter-specific logic
        prompt = self.converter.get_schema_conversion_prompt(
            source_schema,
            csv_summaries,
            str(prev_schema_path) if prev_schema_path else None,
            str(prev_convertor_path) if prev_convertor_path else None,
            pipeline_error
        )
        
        print("=" * 80)
        print("Calling AI agent...")
        print("=" * 80)
        
        # Call AI (use 3 Flash for better schema generation quality)
        response = gemini_inference(prompt, temperature=0.3, enforce_json=True, use_for_schema=True)
        
        # Parse response
        try:
            result = json.loads(response)
            
            # Get keys based on target database type
            schema_key = f"{self.converter.target_type}_schema"
            convertor_key = "data_convertor"
            
            # Try alternate key names for backwards compatibility
            if schema_key not in result:
                # Try without database type prefix
                schema_key = "pg_schema" if "pg_schema" in result else "target_schema"
            
            if convertor_key not in result:
                convertor_key = "csv_convertor" if "csv_convertor" in result else "data_convertor"
            
            return result[schema_key], result[convertor_key]
        except (json.JSONDecodeError, KeyError) as e:
            print(f"Error parsing agent response: {e}")
            print("Raw response:")
            print(response)
            raise

