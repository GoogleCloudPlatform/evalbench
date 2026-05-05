# /// script
# dependencies = ["rich"]
# ///

import sys
import json
from rich import print as rprint


def main():
    """
    A sample validator for PythonScorer.
    Expects JSON input from stdin and writes JSON output to stdout.
    """
    try:
        # Read input from stdin
        input_data = json.load(sys.stdin)

        # Demonstrate using a dependency (rich)
        rprint("[bold green]PythonScorer successfully invoked the sample validator![/bold green]")
        rprint(f"Received scenario ID: [cyan]{input_data.get('id', 'unknown')}[/cyan]")

        # Simple logic: check if generated_eval_result contains "PASS"
        eval_results = input_data.get("generated_eval_result", "")

        if "PASS" in eval_results.upper():
            score = 100.0
            reason = "PASS: The agent satisfied the criteria according to evaluation results."
        else:
            score = 0.0
            reason = "FAIL: The agent failed to satisfy the criteria."

        result = {
            "score": score,
            "reason": reason
        }

        # Output result to stdout
        print(json.dumps(result))

    except Exception as e:
        result = {
            "score": 0.0,
            "reason": f"FAIL: Exception in validator script: {e}"
        }
        print(json.dumps(result))


if __name__ == "__main__":
    main()
