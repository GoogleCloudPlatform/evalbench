import sys
import os
# Add parent directory and parent/evalbench to path to resolve imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../evalbench")))

import logging
import threading
import pandas as pd
from evalbench.generators.models import get_generator
from evalbench.util.config import load_yaml_config

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global models dict for get_generator
global_models = {"lock": threading.Lock(), "registered_models": {}}

def get_summarizer():
    """Loads the generator based on the config in viewer/summarizer_config.yaml."""
    config_path = os.path.join(os.path.dirname(__file__), "config", "summarizer_config.yaml")
    if not os.path.exists(config_path):
        raise FileNotFoundError(f"Config file not found at {config_path}")
        
    config = load_yaml_config(config_path)
    model_config_path = config.get("model_config_path")
    if not model_config_path:
        raise ValueError("model_config_path not specified in summarizer_config.yaml")
        
    # Resolve path relative to the config file if it's relative
    if not os.path.isabs(model_config_path):
        model_config_path = os.path.abspath(os.path.join(os.path.dirname(config_path), model_config_path))
        
    logger.info(f"Loading generator using config: {model_config_path}")
    generator = get_generator(global_models, model_config_path)
    return generator

def summarize_eval_scoring(results_dir):
    """Reads evals.csv and scores.csv from results_dir and generates a summary using Gemini."""
    evals_path = os.path.join(results_dir, "evals.csv")
    scores_path = os.path.join(results_dir, "scores.csv")
    
    if not os.path.exists(evals_path):
        return f"Error: evals.csv not found in {results_dir}"
        
    try:
        evals_df = pd.read_csv(evals_path)
        scores_df = pd.read_csv(scores_path) if os.path.exists(scores_path) else None
        
        # Read prompt from file
        prompt_file = os.path.join(os.path.dirname(__file__), "analyzer.md")
        prompt_instructions = "Analyze and summarize the following evaluation scoring data.\n\nProvide a concise summary of the performance, highlighting key failures or successes."
        if os.path.exists(prompt_file):
            with open(prompt_file, "r") as f:
                prompt_instructions = f.read()
        else:
            logger.warning(f"Prompt file not found at {prompt_file}, using default instructions.")
            
        prompt = prompt_instructions + "\n\n"
        prompt += "### Evals Data (Sample or Summary):\n"
        # Include first few rows or a summary of evals
        prompt += evals_df.head(5).to_string() + "\n\n"
        
        if scores_df is not None:
            prompt += "### Scores Data:\n"
            prompt += scores_df.to_string() + "\n\n"
        
        # Get generator or use API key directly
        from google import genai
        
        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key:
            logger.info("Using GOOGLE_API_KEY for summarization")
            client = genai.Client(api_key=api_key)
            model_name = "gemini-2.5-flash"
        else:
            logger.info("Using default generator from config")
            generator = get_summarizer()
            client = generator.client
            model_name = generator.vertex_model
        
        # Call Gemini directly to bypass sanitize_sql in generate_internal
        logger.info("Calling Gemini for summarization...")
        
        import time
        from google.genai.errors import ClientError
        
        max_retries = 5
        base_delay = 2
        
        for attempt in range(max_retries):
            try:
                response = client.models.generate_content(
                    model=model_name,
                    contents=prompt,
                )
                return response.text
            except ClientError as e:
                if "429" in str(e) and attempt < max_retries - 1:
                    delay = base_delay * (2 ** attempt)
                    logger.warning(f"Rate limit hit (429). Retrying in {delay} seconds... (Attempt {attempt + 1}/{max_retries})")
                    time.sleep(delay)
                else:
                    raise e
            except Exception as e:
                raise e
    except Exception as e:
        logger.exception("Failed to summarize eval scoring")
        return f"Error during summarization: {e}"
    return "Error: Unable to generate summary."

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python summarizer.py <results_dir>")
        sys.exit(1)
        
    results_dir = sys.argv[1]
    summary = summarize_eval_scoring(results_dir)
    print("\n=== Summary ===\n")
    print(summary)
