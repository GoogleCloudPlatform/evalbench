import time
import sys
import os

# Add the directory containing precompute_trends to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import precompute_trends

def main():
    interval = int(os.environ.get("PRECOMPUTE_INTERVAL", 300))
    while True:
        try:
            precompute_trends.precompute()
        except Exception as e:
            print(f"Error in precompute: {e}")
        time.sleep(interval)

if __name__ == "__main__":
    main()
