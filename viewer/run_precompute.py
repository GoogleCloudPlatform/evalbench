import time
import sys
import os

# Add the directory containing precompute_trends to the path
sys.path.append(os.path.dirname(os.path.abspath(__file__)))

import precompute_trends

def main():
    while True:
        try:
            precompute_trends.precompute()
        except Exception as e:
            print(f"Error in precompute: {e}")
        time.sleep(300)

if __name__ == "__main__":
    main()
