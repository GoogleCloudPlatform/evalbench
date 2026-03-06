#!/usr/bin/env python3
"""Extract queries from BIRD dataset for a specific database."""

import json
import csv
import argparse
from pathlib import Path


def extract_queries(json_file: Path, db_id: str, output_csv: Path):
    """Extract queries for a specific database from BIRD JSON.
    
    Args:
        json_file: Path to BIRD dev.json file
        db_id: Database ID to filter (e.g., 'california_schools')
        output_csv: Output CSV file path
    """
    # Read JSON
    with open(json_file) as f:
        data = json.load(f)
    
    # Filter queries for the specified database
    queries = [
        {
            'question_id': item['question_id'],
            'question': item['question'],
            'evidence': item.get('evidence', ''),
            'source_query': item['SQL'],
            'difficulty': item.get('difficulty', '')
        }
        for item in data
        if item['db_id'] == db_id
    ]
    
    if not queries:
        print(f"No queries found for database: {db_id}")
        return
    
    # Write to CSV
    with open(output_csv, 'w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=['question_id', 'question', 'evidence', 'source_query', 'difficulty'])
        writer.writeheader()
        writer.writerows(queries)
    
    print(f"✓ Extracted {len(queries)} queries for '{db_id}'")
    print(f"✓ Saved to: {output_csv}")
    
    # Print statistics
    difficulties = {}
    for q in queries:
        diff = q['difficulty']
        difficulties[diff] = difficulties.get(diff, 0) + 1
    
    print(f"\nQuery Statistics:")
    for diff, count in sorted(difficulties.items()):
        print(f"  {diff}: {count}")


def main():
    parser = argparse.ArgumentParser(
        description='Extract queries from BIRD dataset for a specific database'
    )
    parser.add_argument('json_file', help='Path to BIRD dev.json file')
    parser.add_argument('--db-id', required=True, help='Database ID (e.g., california_schools)')
    parser.add_argument('--output', required=True, help='Output CSV file')
    
    args = parser.parse_args()
    
    extract_queries(
        Path(args.json_file),
        args.db_id,
        Path(args.output)
    )


if __name__ == '__main__':
    main()

