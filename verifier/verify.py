#!/usr/bin/env python3
import os
import sys
import argparse
import pandas as pd


def get_latest_directory(base_dirs):
    """Finds the most recently modified subdirectory among list of potential base directories."""
    all_dirs = []
    for base_dir in base_dirs:
        if os.path.exists(base_dir):
            all_dirs.extend([os.path.join(base_dir, d) for d in os.listdir(base_dir) if os.path.isdir(os.path.join(base_dir, d))])
    return max(all_dirs, key=os.path.getmtime) if all_dirs else None


def main():
    parser = argparse.ArgumentParser(description="Verifies that SQL was successfully generated.")
    parser.add_argument("path", nargs="?", help="Session folder")
    args = parser.parse_args()

    session_dir = args.path
    if not session_dir:
        search_locations = ["/tmp_session_files/results", "results"]
        print("No manual path provided. Searching latest session...")
        session_dir = get_latest_directory(search_locations)

    if not session_dir or not os.path.exists(session_dir):
        print("ERROR: Invalid session path. Cannot proceed.")
        sys.exit(1)

    print(f"Inspecting session: {os.path.abspath(session_dir)}")

    evals_path = os.path.join(session_dir, "evals.csv")
    if not os.path.exists(evals_path):
        print(f"❌ CRITICAL FAILURE: The 'evals.csv' result file could not be found in the directory.")
        sys.exit(1)

    try:
        # Load dataset
        df = pd.read_csv(evals_path, low_memory=False)

        # 1. Assert the column exists
        TARGET_COL = "generated_sql"
        if TARGET_COL not in df.columns:
            print(f"❌ CRITICAL FAILURE: Column '{TARGET_COL}' is missing entirely from output file.")
            sys.exit(1)

        total_rows = len(df)
        if total_rows == 0:
            print(f"❌ CRITICAL FAILURE: The result file is completely empty (0 rows).")
            sys.exit(1)

        # 2. Assert content is present in the column
        # We filter out truly NULL values and handle any rows where generation literally never fired.
        non_null_count = df[TARGET_COL].notna().sum()
        null_count = total_rows - non_null_count

        if null_count > 0:
            print(f"\n❌ VERIFICATION FAILED!")
            print(f"   {null_count} of {total_rows} scenarios failed to produce ANY sql content (NULL detected).")

            # Sample of failing rows
            failing_mask = df[TARGET_COL].isna()
            if "id" in df.columns:
                failing_ids = df.loc[failing_mask, "id"].head(5).tolist()
                print(f"   Sample failing evaluation IDs: {failing_ids}")
            sys.exit(1)

        # Optional Check: also inspect for explicitly empty strings if user considers that a failure
        empty_str_mask = df[TARGET_COL].astype(str).str.strip() == ""
        empty_str_count = empty_str_mask.sum()
        if empty_str_count > 0:
            print(f"\n❌ VERIFICATION FAILED!")
            print(f"   {empty_str_count} rows generated an empty string \"\" instead of SQL.")
            sys.exit(1)

        print(f"\n✅ VERIFICATION SUCCESSFUL!")
        print(f"   Successfully verified {total_rows} rows. 100% have content in '{TARGET_COL}'.")
        sys.exit(0)

    except Exception as e:
        print(f"❌ UNKNOWN SYSTEM ERROR reading results: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
