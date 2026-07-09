"""
EffectiveBilledTokens Scorer

Normalizes total processed tokens by their precise financial weighting: cache
reads are significantly cheaper than fresh input, while cache writes and output
tokens command a premium. This condenses the multi-tiered pricing model into a
single, model-agnostic index directly correlated with real-world dollar spend.

The default weights mirror Anthropic Opus price ratios (input=1.0x):
    cache read   = 0.10x   (cheap replay of cached context)
    cache write  = 1.25x   (premium to establish a cache entry)
    output       = 5.00x   (generated tokens cost the most)
They are overridable per-run via the scorer config keys ``input_weight``,
``cached_weight``, ``cache_write_weight``, and ``output_weight``.
"""
from typing import Tuple, Any
from scorers import comparator
import json


class EffectiveBilledTokens(comparator.Comparator):
    """
    EffectiveBilledTokens class implements the Comparator base class for a
    price-weighted index of tokens processed by the model.
    """

    def __init__(self, config: dict):
        self.name = "effective_billed_tokens"
        self.config = config or {}
        # Anthropic Opus price ratios (relative to fresh input = 1.0),
        # overridable per-run.
        self.input_weight = float(self.config.get("input_weight", 1.0))
        self.cached_weight = float(self.config.get("cached_weight", 0.1))
        self.cache_write_weight = float(
            self.config.get("cache_write_weight", 1.25)
        )
        self.output_weight = float(self.config.get("output_weight", 5.0))

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
        Calculates price-weighted effective billed tokens from the history.

        Args:
            generated_eval_result: String representing JSON history.

        Returns:
            Tuple (score, explanation) where score is the weighted token index.
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
            invalid_agent_payloads = 0

            if isinstance(history, list):
                for turn in history:
                    agent_resp = turn.get("agent", "")
                    try:
                        resp_json = json.loads(agent_resp)
                        stats = resp_json.get("stats", {})
                        models = stats.get("models", {})
                        for model_stats in models.values():
                            tokens = model_stats.get("tokens", {})
                            # Use `input` (not `prompt`, its duplicate) so
                            # fresh input isn't double-counted. Missing cache
                            # keys default to 0, so generators that omit them
                            # reduce to input + weighted output.
                            total_tokens += (
                                tokens.get("input", 0.0) * self.input_weight
                                + tokens.get("cached", 0.0) * self.cached_weight
                                + tokens.get("cache_creation", 0.0)
                                * self.cache_write_weight
                                + tokens.get("candidates", 0.0)
                                * self.output_weight
                            )
                    except json.JSONDecodeError:
                        invalid_agent_payloads += 1

                skipped_note = (
                    f" Skipped {invalid_agent_payloads} turn(s) with invalid "
                    f"agent JSON."
                    if invalid_agent_payloads
                    else ""
                )
                return float(total_tokens), (
                    f"Effective billed tokens: {total_tokens} "
                    f"(weighted: input={self.input_weight}, "
                    f"cached={self.cached_weight}, "
                    f"cache_write={self.cache_write_weight}, "
                    f"output={self.output_weight})."
                    f"{skipped_note}"
                )
            else:
                return 0.0, "Conversation history is not a list."
        except json.JSONDecodeError:
            return 0.0, "Failed to parse conversation history as JSON."
