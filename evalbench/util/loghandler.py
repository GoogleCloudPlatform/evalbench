import json


def truncateExecutionOutputs(eval_output, config):
    for key in [
        "generated_result",
        "golden_result",
        "golden_eval_results",
        "eval_results",
    ]:
        truncated_result_count = 250
        if (
            "reporting" in config
            and "truncate_execution_outputs" in config["reporting"]
        ):
            truncated_result_count = config["reporting"]["truncate_execution_outputs"]
        if key in eval_output and isinstance(eval_output[key], list):
            suffix = ""
            if len(eval_output[key]) > truncated_result_count:
                suffix = f"...and {len(eval_output[key]) - truncated_result_count} more items truncated"
            eval_output[key] = (
                json.dumps(eval_output[key]
                           [:truncated_result_count], default=str)
                + suffix
            )
