import sys
import os
import logging
import threading
import pandas as pd

# Add parent directory and parent/evalbench to path to resolve imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../evalbench")))

from evalbench.generators.models import get_generator
from evalbench.util.config import load_yaml_config

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global models dict for get_generator
global_models = {"lock": threading.Lock(), "registered_models": {}}

def get_results_dir():
    # Try to read from environment variable
    res_dir = os.environ.get("RESULTS_DIR")
    if res_dir:
        return res_dir
        
    # Check multiple locations for results directory
    results_dir_candidates = [
        "/tmp_session_files/results",
        os.path.join(os.path.dirname(os.path.dirname(__file__)), "results"),
        os.path.join(os.getcwd(), "results"),
        "/evalbench/results"
    ]
    
    for candidate in results_dir_candidates:
        if os.path.exists(candidate):
            logger.info(f"Found results directory at: {candidate}")
            return candidate
            
    logger.warning("Results directory not found in candidates, defaulting to current directory results")
    return os.path.join(os.getcwd(), "results")

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

def compare_evals(id1, id2):
    """Compares two evaluation runs using Gemini."""
    results_dir = get_results_dir()
    
    path1 = os.path.join(results_dir, id1)
    path2 = os.path.join(results_dir, id2)
    
    if not os.path.exists(path1):
        return f"Error: Directory for {id1} not found at {path1}"
    if not os.path.exists(path2):
        return f"Error: Directory for {id2} not found at {path2}"
        
    try:
        evals1 = pd.read_csv(os.path.join(path1, "evals.csv"))
        scores1 = pd.read_csv(os.path.join(path1, "scores.csv")) if os.path.exists(os.path.join(path1, "scores.csv")) else None
        
        evals2 = pd.read_csv(os.path.join(path2, "evals.csv"))
        scores2 = pd.read_csv(os.path.join(path2, "scores.csv")) if os.path.exists(os.path.join(path2, "scores.csv")) else None
        
        prompt_file = os.path.join(os.path.dirname(__file__), "config", "ai_comparer.md")
        prompt_instructions = "Compare the following two evaluation runs. Highlight differences in performance, errors, and trajectories."
        if os.path.exists(prompt_file):
            with open(prompt_file, "r") as f:
                prompt_instructions = f.read()
        else:
            logger.warning(f"Prompt file not found at {prompt_file}, using default instructions.")
            
        prompt = prompt_instructions + "\n\n"
        
        prompt += f"### Run 1: {id1}\n"
        prompt += evals1.head(5).to_string() + "\n\n"
        if scores1 is not None:
            prompt += "Scores:\n" + scores1.to_string() + "\n\n"
            
        prompt += f"### Run 2: {id2}\n"
        prompt += evals2.head(5).to_string() + "\n\n"
        if scores2 is not None:
            prompt += "Scores:\n" + scores2.to_string() + "\n\n"
            
        # Get generator or use API key directly
        from google import genai
        
        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key:
            logger.info("Using GOOGLE_API_KEY for comparison")
            client = genai.Client(api_key=api_key)
            model_name = "gemini-2.5-flash"
        else:
            logger.info("Using default generator from config")
            generator = get_summarizer()
            client = generator.client
            model_name = generator.vertex_model
            
        logger.info(f"Calling Gemini ({model_name}) for comparison...")
        
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
        logger.exception("Failed to compare evals")
        return f"Error during comparison: {e}"
        
    return "Error: Unable to generate comparison."

if __name__ == "__main__":
    if len(sys.argv) < 3:
        print("Usage: python ai_comparer.py <id1> <id2>")
        sys.exit(1)
        
    id1 = sys.argv[1]
    id2 = sys.argv[2]
    result = compare_evals(id1, id2)
    print("\n=== Comparison ===\n")
    print(result)
