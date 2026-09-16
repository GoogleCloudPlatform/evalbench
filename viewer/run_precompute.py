import time
import sys
import os

# Add the directory containing precompute_trends to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import precompute_trends
import precompute_dataset_quality
from precompute_lease import PrecomputeLease

def main():
    interval = int(os.environ.get("PRECOMPUTE_INTERVAL", 300))
    results_dir = precompute_trends.get_results_dir()
    while True:
        # Every instance runs this loop, but only the lease holder may write.
        with PrecomputeLease(results_dir) as lease:
            if lease:
                # Dataset quality runs first because it is cheap and always finishes.
                # The trends pass makes an LLM call per unprocessed run and gets
                # SIGKILLed mid-pass on a large backlog, which the try/except below
                # cannot catch and which would otherwise starve every pass after it.
                for precompute in (
                    precompute_dataset_quality.precompute,
                    precompute_trends.precompute,
                ):
                    try:
                        precompute()
                    except Exception as e:
                        print(f"Error in {precompute.__module__}: {e}")
        time.sleep(interval)

if __name__ == "__main__":
    main()
