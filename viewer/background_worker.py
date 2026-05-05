import time
import sys
import os
import logging

# Add current dir to path
current_dir = os.path.dirname(os.path.abspath(__file__))
sys.path.append(current_dir)

import precompute_trends

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

def main():
    while True:
        logging.info("Starting precomputation...")
        try:
            precompute_trends.precompute()
        except Exception as e:
            logging.error(f"Error during precomputation: {e}")
        logging.info("Precomputation finished. Sleeping for 5 minutes...")
        time.sleep(300)

if __name__ == "__main__":
    main()
