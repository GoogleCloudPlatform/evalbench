"""AnalyticsScorer: Exact implementation of Brewmax Data Result Rater for Evalbench.

Grades each generated execution result against the golden execution result using the
Conversational Analytics Content/Data Results rubric and side-by-side LLM validator.
"""

import json
import logging
import re
from typing import Any, Tuple

from databases.util import get_cache_client
from generators.models import get_generator
from scorers import comparator
from scorers.prompt.analyticsscorer import (
    ANALYTICS_SCORER_PROMPT_TEMPLATE,
    DATA_RESULTS_RUBRIC,
)
from scorers.util import with_cache_execute


class AnalyticsScorer(comparator.Comparator):
    """AnalyticsScorer implements the Brewmax Data Result AutoRater for Evalbench."""

    def __init__(self, config: dict, global_models: Any):
        super().__init__(config)
        self.name = "analytics_scorer"
        self.config = config or {}
        self.model_config = self.config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for AnalyticsScorer")
        self.model = get_generator(global_models, self.model_config)
        self.cache_client = get_cache_client(self.config)
        self.max_data_chars = self.config.get("max_data_chars", 8000)
        self.query_label = self.config.get("query_label", "SQL Query")

    def _render_data(self, data: Any) -> str:
        """Formats the data payload into a string representation, capped at max_data_chars."""
        if data is None:
            data_str = ""
        elif isinstance(data, (list, dict)):
            try:
                data_str = json.dumps(data, default=str)
            except Exception:
                data_str = str(data)
        else:
            data_str = str(data)

        if len(data_str) > self.max_data_chars:
            data_str = (
                f"{data_str[:self.max_data_chars]}... [truncated "
                f"{len(data_str) - self.max_data_chars} chars]"
            )
        return data_str

    def _render_trajectory(self, query: str, data: Any, query_label: str = "SQL Query") -> str:
        """Renders the query and data as a formatted block for rubric evaluation."""
        rendered_data = self._render_data(data)
        query_str = query or ""
        return f"{query_label}:\n{query_str}\nData:\n{rendered_data}"

    def _parse_verdict(self, response_text: str) -> Tuple[float, str]:
        """Parses the VERDICT: PASS / FAIL label from the LLM autorater response."""
        if not response_text:
            return 0.0, "Empty response from autorater model."

        cleaned = response_text.strip()
        matches = re.findall(r"VERDICT:\s*(PASS|FAIL)\b", cleaned, re.IGNORECASE)
        if matches:
            verdict = matches[-1].upper()
            score = 100.0 if verdict == "PASS" else 0.0
            return score, response_text

        # Fallback heuristic if explicit label prefix is omitted
        if "PASS" in cleaned.upper() and "FAIL" not in cleaned.upper():
            return 100.0, response_text
        return 0.0, response_text

    def _inference_without_caching(self, prompt: str) -> str:
        if self.model is None:
            raise RuntimeError("Model not initialized for AnalyticsScorer")
        response = self.model.generate(prompt)
        return getattr(response, "stdout", response) if response else ""

    def compare(
        self,
        nl_prompt: Any,
        golden_query: Any,
        query_type: Any,
        golden_execution_result: Any,
        golden_eval_result: Any,
        golden_error: Any,
        generated_query: Any,
        generated_execution_result: Any,
        generated_eval_result: Any,
        generated_error: Any,
        database: str = "",
        **kwargs,
    ) -> Tuple[float, str]:
        """Evaluates trial result against golden reference following the Brewmax rubric."""
        has_golden_data = bool(golden_execution_result)

        # Handle execution error conditions per rubric
        if golden_error:
            return 0.0, f"Golden query failed to execute: {golden_error}"
        if generated_error and has_golden_data:
            return 0.0, f"Generated query failed to execute: {generated_error}"

        golden_trajectory = self._render_trajectory(
            query=golden_query or "",
            data=golden_execution_result,
            query_label=self.query_label,
        )
        trial_trajectory = self._render_trajectory(
            query=generated_query or "",
            data=generated_execution_result,
            query_label=self.query_label,
        )

        prompt = ANALYTICS_SCORER_PROMPT_TEMPLATE.format(
            rubric=DATA_RESULTS_RUBRIC,
            user_prompt=nl_prompt or "",
            ground_truth_trajectory=golden_trajectory,
            trial_trajectory=trial_trajectory,
        )

        logging.debug("\n --------- Analytics Scorer Prompt: --------- \n %s", prompt)

        try:
            if self.cache_client:
                response = with_cache_execute(
                    prompt,
                    self.model_config,
                    self._inference_without_caching,
                    self.cache_client,
                )
            else:
                response = self._inference_without_caching(prompt)

            logging.debug("\n --------- Analytics Scorer Response: --------- \n %s", response)
            return self._parse_verdict(response)

        except Exception as e:
            logging.error("AnalyticsScorer evaluation failed: %s", e)
            return 0.0, f"Error calling autorater model: {e}"
