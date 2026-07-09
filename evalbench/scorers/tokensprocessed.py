"""
TokensProcessed Scorer

Measures every token the model evaluates per user journey, including fully
cached context layers, completely unweighted. Unlike token_consumption (which
counts only fresh input + output), this surfaces the cached system prompt,
metadata, and tool schemas served from the provider's prompt cache -- serving
as an absolute index of the physical compute work required by the model.
"""
from typing import Tuple, Any
from scorers import comparator
import json


class TokensProcessed(comparator.Comparator):
    """
    TokensProcessed class implements the Comparator base class for measuring
    total tokens processed by the model, cached layers included.
    """

    def __init__(self, config: dict):
        self.name = "tokens_processed"
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
        Calculates total tokens processed from the conversation history.

        Sums fresh input, output, cache-read, and cache-creation tokens across
        every model bucket of every turn -- all unweighted.

        Args:
            generated_eval_result: String representing JSON history.

        Returns:
            Tuple (score, explanation) where score is the tokens processed.
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
                        for model_stats in models.values():
                            tokens = model_stats.get("tokens", {})
                            # Use `input` (not `prompt`, its duplicate) plus
                            # output and both cache tiers so nothing is
                            # double-counted. Missing cache keys default to 0,
                            # so generators that omit them degrade to
                            # input + output.
                            total_tokens += (
                                tokens.get("input", 0.0)
                                + tokens.get("candidates", 0.0)
                                + tokens.get("cached", 0.0)
                                + tokens.get("cache_creation", 0.0)
                            )
                    except json.JSONDecodeError:
                        malformed_agent_entries += 1

                malformed_note = (
                    f" Skipped {malformed_agent_entries} malformed agent "
                    f"response(s)."
                    if malformed_agent_entries
                    else ""
                )
                return float(total_tokens), (
                    f"Agent processed {total_tokens} tokens "
                    f"(incl. cached context).{malformed_note}"
                )
            else:
                return 0.0, "Conversation history is not a list."
        except json.JSONDecodeError:
            return 0.0, "Failed to parse conversation history as JSON."
