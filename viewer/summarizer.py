import sys
import os
# Add parent directory and parent/evalbench to path to resolve imports
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))
sys.path.append(os.path.abspath(os.path.join(os.path.dirname(__file__), "../evalbench")))

import logging
import threading
import pandas as pd
from evalbench.util.config import load_yaml_config
from evalbench.generators.models import get_generator

# Setup logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# Global models dict for get_generator
global_models = {"lock": threading.Lock(), "registered_models": {}}

# Bounds on how much of scores.csv is rendered into the prompt.
#
# Three bounds because each covers a different shape of run: cell width for a
# run with one huge blob, row count for a run with many results, and total
# characters as a backstop for anything that slips past both. The analyzer
# prompt asks for per-comparator scores and representative failures, so
# truncated cells cost it nothing.
SCORES_MAX_COLWIDTH = 500
SCORES_MAX_ROWS = 500
SCORES_MAX_CHARS = 200_000


def render_scores_for_prompt(scores_df):
    """Render scores.csv for the analyzer prompt within a bounded size."""
    body = scores_df.head(SCORES_MAX_ROWS).to_string(
        max_colwidth=SCORES_MAX_COLWIDTH
    )
    omitted = len(scores_df) - SCORES_MAX_ROWS
    if omitted > 0:
        body += f"\n... {omitted} further rows omitted ..."
    if len(body) > SCORES_MAX_CHARS:
        body = body[:SCORES_MAX_CHARS] + "\n... truncated ..."
    return body

def get_summarizer(results_dir: str = None, dataset_name: str = None, model_config_path: str = None):
    """Loads the generator based on explicit parameter, run_config.yaml, dataset_models mapping, or viewer/config/summarizer_config.yaml fallback."""
    selected_config_path = model_config_path
    base_dir = os.getcwd() if selected_config_path else None

    # Check run_config.yaml inside results_dir if provided
    if not selected_config_path and results_dir and os.path.exists(results_dir):
        run_config_file = os.path.join(results_dir, "run_config.yaml")
        if os.path.exists(run_config_file):
            try:
                run_config = load_yaml_config(run_config_file)
                selected_config_path = run_config.get("summarizer_model_config")
                if selected_config_path:
                    base_dir = results_dir
                    logger.info(f"Found run-specific summarizer model config: {selected_config_path}")
            except Exception as e:
                logger.warning(f"Could not load run_config.yaml from {run_config_file}: {e}")

    # Check viewer/config/summarizer_config.yaml for dataset_models mapping or default model_config_path
    config_path = os.path.join(os.path.dirname(__file__), "config", "summarizer_config.yaml")
    if os.path.exists(config_path):
        config = load_yaml_config(config_path)

        if not selected_config_path and dataset_name:
            dataset_models = config.get("dataset_models") or {}
            if dataset_name in dataset_models:
                selected_config_path = dataset_models[dataset_name]
                base_dir = os.path.dirname(config_path)
                logger.info(f"Found dataset-specific summarizer model config for '{dataset_name}': {selected_config_path}")

        if not selected_config_path:
            selected_config_path = config.get("model_config_path")
            base_dir = os.path.dirname(config_path)

    if not selected_config_path:
        raise ValueError("No valid model_config_path specified for summarizer.")

    # Resolve path relative to base_dir if it's relative
    if not os.path.isabs(selected_config_path):
        if not base_dir:
            base_dir = os.path.dirname(config_path) if os.path.exists(config_path) else os.path.dirname(__file__)
        selected_config_path = os.path.abspath(os.path.join(base_dir, selected_config_path))

    logger.info(f"Loading generator using config: {selected_config_path}")
    generator = get_generator(global_models, selected_config_path)
    return generator

def summarize_eval_scoring(results_dir, dataset_name=None, model_config_path=None):
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
            prompt += render_scores_for_prompt(scores_df) + "\n\n"

        # Get generator or use API key directly
        from google import genai

        api_key = os.environ.get("GOOGLE_API_KEY")
        if api_key:
            logger.info("Using GOOGLE_API_KEY for summarization")
            client = genai.Client(api_key=api_key)
            model_name = "gemini-2.5-flash"
        else:
            logger.info("Using generator from config")
            generator = get_summarizer(results_dir=results_dir, dataset_name=dataset_name, model_config_path=model_config_path)
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
