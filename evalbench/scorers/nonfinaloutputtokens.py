"""
NonFinalOutputTokens Scorer

Measures the output tokens spent on the *path* to the answer -- reasoning and
tool-call emission -- while excluding the final rendered response text, whose
verbosity is a presentation choice rather than a signal of how efficiently the
agent fulfilled the request.

Rationale: two runs may invoke the identical tool calls yet differ wildly in how
much they narrate the result (a terse summary vs. a full table view). That
variance lands entirely in output tokens and makes ``token_consumption`` noisy
for side-by-side efficiency comparison -- especially for one-shot runs.
Subtracting an estimate of each turn's user-facing ``response`` text isolates the
stable "work" component (output spent on reasoning and emitting tool calls; note
that tool-call arguments are counted as output but are not part of ``response``).

The response-size estimate reuses the same ``len / 4`` chars-per-token heuristic
as ``scorers.mcp_tool_metrics`` so token approximations stay consistent across
the codebase.
"""
from typing import Tuple, Any
from scorers import comparator
import json

# Rough chars-per-token heuristic for estimating the response's token footprint.
# Mirrors ``scorers.mcp_tool_metrics._CHARS_PER_TOKEN``.
_CHARS_PER_TOKEN = 4


class NonFinalOutputTokens(comparator.Comparator):
    """
    NonFinalOutputTokens class implements the Comparator base class for measuring
    output tokens excluding the final rendered response text.
    """

    def __init__(self, config: dict):
        self.name = "non_final_output_tokens"
        self.config = config

    def compare(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: Any,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: Any,
        generated_eval_result: str,
        generated_error: str,
    ) -> Tuple[float, str]:
        """
        Calculates non-final output tokens from the conversation history.

        For each turn, sums output tokens (``candidates``) across model buckets
        and subtracts an estimate of the turn's user-facing ``response`` text.
        The per-turn contribution is clamped at zero so heuristic overshoot never
        drives the total negative.

        Args:
            generated_eval_result: String representing JSON history.

        Returns:
            Tuple (score, explanation) where score is the non-final output tokens.
        """
        if generated_error:
            return 0.0, f"Generation error: {generated_error}"

        if not generated_eval_result:
            return 0.0, "No conversation history provided."

        try:
            history = (
                json.loads(generated_eval_result)
                if isinstance(generated_eval_result, str)
                else generated_eval_result
            )
            if isinstance(history, dict):
                history = history.get("conversation_history", "[]")
            if isinstance(history, str):
                history = json.loads(history)

            total_tokens = 0.0
            malformed_agent_entries = 0

            if isinstance(history, list):
                for turn in history:
                    agent_resp = turn.get("agent", "")
                    try:
                        resp_json = json.loads(agent_resp)
                        stats = resp_json.get("stats", {})
                        models = stats.get("models", {})
                        turn_output = 0.0
                        for model_stats in models.values():
                            tokens = model_stats.get("tokens", {})
                            # `candidates` is the output channel. Tool-call
                            # arguments are counted here but are not part of
                            # `response`, so they survive the subtraction.
                            turn_output += tokens.get("candidates", 0.0)

                        response_text = resp_json.get("response", "") or ""
                        est_response = round(
                            len(response_text) / _CHARS_PER_TOKEN
                        )
                        # Clamp so a response estimate exceeding the reported
                        # output never pushes the running total negative.
                        total_tokens += max(0.0, turn_output - est_response)
                    except json.JSONDecodeError:
                        malformed_agent_entries += 1

                malformed_note = (
                    f" Skipped {malformed_agent_entries} malformed agent "
                    f"response(s)."
                    if malformed_agent_entries
                    else ""
                )
                return float(total_tokens), (
                    f"Non-final output: {total_tokens} tokens "
                    f"(output excl. est. response text).{malformed_note}"
                )
            else:
                return 0.0, "Conversation history is not a list."
        except json.JSONDecodeError:
            return 0.0, "Failed to parse conversation history as JSON."
