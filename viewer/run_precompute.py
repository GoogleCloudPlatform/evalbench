import time
import sys
import os

# Add the directory containing precompute_trends to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import precompute_trends
import precompute_dataset_quality

def main():
    interval = int(os.environ.get("PRECOMPUTE_INTERVAL", 300))
    while True:
        # Each pass is guarded separately so one failing doesn't starve the other.
        for precompute in (
            precompute_trends.precompute,
            precompute_dataset_quality.precompute,
        ):
            try:
                precompute()
            except Exception as e:
                print(f"Error in {precompute.__module__}: {e}")
        time.sleep(interval)

if __name__ == "__main__":
    main()
