"""AnalyticsScorer: Conversational Analytics Data Results Rater for Evalbench.

Grades each generated execution result against the golden execution result using the
Conversational Analytics Content/Data Results rubric and side-by-side LLM validator.
"""

import json
import logging
import re
from typing import Any, Tuple

from databases.util import get_cache_client
from generators.models import get_generator
from scorers import comparator, setmatcher
from scorers.prompt.analyticsscorer import (
    ANALYTICS_SCORER_PROMPT_TEMPLATE,
    DATA_RESULTS_RUBRIC,
)
from scorers.util import make_hashable, with_cache_execute


class AnalyticsScorer(comparator.Comparator):
    """AnalyticsScorer implements the Conversational Analytics Data Results AutoRater for Evalbench."""

    def __init__(self, config: dict, global_models: Any):
        super().__init__(config)
        self.name = "analytics_scorer"
        self.config = config or {}
        self.model_config = self.config.get("model_config") or ""
        if not self.model_config:
            raise ValueError("model_config is required for AnalyticsScorer")
        self.model = get_generator(global_models, self.model_config)
        self.cache_client = get_cache_client(self.config)
        self.max_rows = self.config.get("max_rows", 50)
        self.query_label = self.config.get("query_label", "SQL Query")
        self.set_match_checker = setmatcher.SetMatcher({})

    @staticmethod
    def take_n_uniques(output_list: list, n: int) -> list:
        """Takes n number of unique (non duplicate) values from the output list.

        Args:
          output_list: The execution output result set
          n: Max number of unique values needed.

        Returns:
          The execution output result set without duplicates in a size of n values or less.
        """
        seen_dicts = set()
        new_list = []
        for d in output_list:
            if isinstance(d, dict):
                t = frozenset((k, make_hashable(v)) for k, v in d.items())
                if t not in seen_dicts:
                    seen_dicts.add(t)
                    new_list.append(d)
                    if len(new_list) == n:
                        break
            else:
                new_list.append(d)
                if len(new_list) == n:
                    break
        return new_list

    def _render_data(self, data: Any) -> str:
        """Formats the data payload into a valid JSON string representation, truncating at row level."""
        if data is None:
            return ""

        if isinstance(data, list):
            total_rows = len(data)
            if total_rows == 0:
                return "[]"
            truncated_data = self.take_n_uniques(data, self.max_rows)
            data_json = json.dumps(truncated_data, default=str)
            if total_rows > len(truncated_data):
                return (
                    f"{data_json}\n[Note: Displaying {len(truncated_data)} of "
                    f"{total_rows} total rows]"
                )
            return data_json

        if isinstance(data, dict):
            return json.dumps(data, default=str)

        return str(data)

    def _render_trajectory(self, query: str, data: Any) -> str:
        """Renders the query and data as a formatted block for rubric evaluation."""
        rendered_data = self._render_data(data)
        query_str = query or ""
        return f"{self.query_label}:\n{query_str}\nData:\n{rendered_data}"

    def _parse_verdict(self, response_text: str) -> Tuple[float, str]:
        """Parses the VERDICT: PASS / FAIL label from the LLM autorater response."""
        if not response_text:
            return 0.0, "Could not parse valid VERDICT: empty response from autorater."

        cleaned = response_text.strip()
        matches = re.findall(r"VERDICT:\s*(PASS|FAIL)\b", cleaned, re.IGNORECASE)
        if matches:
            verdict = matches[-1].upper()
            score = 100.0 if verdict == "PASS" else 0.0
            return score, response_text

        # Check for standalone line PASS/FAIL at the end
        lines = [line.strip() for line in cleaned.splitlines() if line.strip()]
        if lines:
            last_line = lines[-1].upper()
            if re.fullmatch(r"(VERDICT:\s*)?PASS\b.*", last_line):
                return 100.0, response_text
            if re.fullmatch(r"(VERDICT:\s*)?FAIL\b.*", last_line):
                return 0.0, response_text

        return 0.0, f"Could not parse valid VERDICT from autorater response:\n{response_text}"

    def _is_exact_match(
        self,
        nl_prompt: str,
        golden_query: str,
        query_type: str,
        golden_execution_result: list,
        golden_eval_result: str,
        golden_error: str,
        generated_query: str,
        generated_execution_result: list,
        generated_eval_result: str,
        generated_error: str,
    ) -> bool:
        score, _ = self.set_match_checker.compare(
            nl_prompt,
            golden_query,
            query_type,
            golden_execution_result,
            golden_eval_result,
            golden_error,
            generated_query,
            generated_execution_result,
            generated_eval_result,
            generated_error,
        )
        return score == 100

    def _inference_without_caching(self, prompt: str) -> str:
        if self.model is None:
            raise RuntimeError("Model not initialized for AnalyticsScorer")
        return self.model.generate(prompt)

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
        """Evaluates trial result against golden reference following the Conversational Analytics rubric."""
        # 1. Unconditional execution error checks
        if golden_error:
            return 0.0, f"Golden query failed to execute: {golden_error}"
        if generated_error:
            return 0.0, f"Generated query failed to execute: {generated_error}"

        # 2. Fast short-circuit: if exact set match on non-empty results, skip LLM
        is_empty_results = (not golden_execution_result) and (not generated_execution_result)
        if not is_empty_results and isinstance(golden_execution_result, list) and isinstance(generated_execution_result, list):
            if self._is_exact_match(
                nl_prompt,
                golden_query,
                query_type,
                golden_execution_result,
                golden_eval_result,
                golden_error,
                generated_query,
                generated_execution_result,
                generated_eval_result,
                generated_error,
            ):
                return 100.0, "Skipped. Exact Match was found."

        # 3. Format trajectories
        golden_trajectory = self._render_trajectory(
            query=golden_query or "",
            data=golden_execution_result,
        )
        trial_trajectory = self._render_trajectory(
            query=generated_query or "",
            data=generated_execution_result,
        )

        prompt = ANALYTICS_SCORER_PROMPT_TEMPLATE.format(
            rubric=DATA_RESULTS_RUBRIC,
            user_prompt=nl_prompt or "",
            ground_truth_trajectory=golden_trajectory,
            trial_trajectory=trial_trajectory,
        )

        logging.debug("\n --------- Analytics Scorer Prompt: --------- \n %s", prompt)

        # 4. LLM inference with caching (exceptions propagate to score.compare)
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
