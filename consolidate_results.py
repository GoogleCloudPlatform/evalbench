import pandas as pd
import glob
import os
import re

def extract_question(prompt_text):
    if not isinstance(prompt_text, str): return prompt_text
    # Prompts usually end with the question after "for the following question:" or similar.
    # Or just take the last few lines.
    # In Air Travel, the question is usually after the last '**************************'
    parts = prompt_text.split('**************************')
    if len(parts) > 2:
        return parts[-1].strip()
    return prompt_text[:100] + "..." # Fallback

def load_dataset_results(base_path, dataset_name):
    dirs = glob.glob(f"{base_path}/*")
    if not dirs: return None
    latest = max(dirs, key=os.path.getmtime)
    
    evals_df = pd.read_csv(f"{latest}/evals.csv")
    scores_df = pd.read_csv(f"{latest}/scores.csv")
    
    scores_pivot = scores_df.pivot_table(index=['id', 'dialects'], columns='comparator', values='score').reset_index()
    merged = pd.merge(evals_df, scores_pivot, on=['id', 'dialects'], how='left')
    
    # Extract clean question
    merged['nl_prompt'] = merged['nl_prompt'].apply(extract_question)
    merged['Dataset'] = dataset_name
    return merged

def consolidate():
    df_air_full = load_dataset_results("results/air_travel_full", "Air Travel")
    df_air_sqlite = load_dataset_results("results/air_travel_sqlite", "Air Travel")
    
    df_bat_full = load_dataset_results("results/bat_full", "BAT")
    df_bat_sqlite = load_dataset_results("results/bat_sqlite", "BAT")
    
    df_bird = load_dataset_results("results/bird_subset", "BIRD")
    
    dfs = [df for df in [df_air_full, df_air_sqlite, df_bat_full, df_bat_sqlite, df_bird] if df is not None]
    if not dfs:
        print("No results found.")
        return
        
    df_full = pd.concat(dfs, ignore_index=True)
    
    cols = [
        'Dataset', 'id', 'nl_prompt', 'golden_sql', 'dialects', 
        'generated_sql', 'exact_match', 'set_match', 'executable_sql', 
        'generated_error'
    ]
    available_cols = [c for c in cols if c in df_full.columns]
    df_out = df_full[available_cols]
    
    rename_map = {
        'id': 'Query ID',
        'nl_prompt': 'Question',
        'golden_sql': 'Gold SQL',
        'dialects': 'Database',
        'generated_sql': 'Generated SQL',
        'generated_error': 'Error'
    }
    df_out = df_out.rename(columns=rename_map)
    
    df_out.to_csv("consolidated_results.csv", index=False)
    print("Consolidated results saved to consolidated_results.csv")

if __name__ == "__main__":
    consolidate()
