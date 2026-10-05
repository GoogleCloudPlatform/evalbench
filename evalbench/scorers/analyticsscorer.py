"""AnalyticsScorer: Conversational Analytics Data Results Rater for Evalbench.

Grades each generated execution result against the golden execution result using the
Conversational Analytics "Content/Data Results" rubric
(google3/storage/evals/scoring/rubrics/ca/accuracy/content/data_results.textproto)
and a side-by-side LLM validator modeled on the Cortado turn-level rubric autorater.

Trajectories are rendered the same way Cortado renders them for the autorater:
quoted `SQL Query:` / `Data:` blocks, data formatted with pandas `to_string`,
cell-budget truncation with a "(<side> dataframe was truncated from N rows to
M rows for display.)" note, and the `Data:` block omitted when the query
returned zero rows.
"""

import logging
import re
from typing import Any, Tuple

import pandas as pd

from databases.util import get_cache_client
from generators.models import get_generator
from scorers import comparator, setmatcher
from scorers.prompt.analyticsscorer import (
    ANALYTICS_SCORER_PROMPT_TEMPLATE,
    DATA_RESULTS_RUBRIC,
)
from scorers.util import with_cache_execute

# Mirrors Cortado's MAX_DATA_RESULT_ENTRIES: the maximum number of cells
# (rows x columns) rendered per data result before truncation.
DEFAULT_MAX_DATA_RESULT_ENTRIES = 50

# Mirrors Cortado's trajectory_templates.py (including its whitespace).
_SINGLE_QUERY_TEMPLATE = """
    {query_label}:
    "{query}"
"""

_SINGLE_DATA_RESULT_TEMPLATE = """
     Data:
    "{data}"
"""

_EMPTY_TRIAL_JUSTIFICATION = (
    "The trial trajectory is empty and the golden trajectory is not empty."
    " Thus, the rubric criterion is not met."
)

_VERDICT_RE = re.compile(r"VERDICT\s*:\s*(PASS|FAIL)\b", re.IGNORECASE)
_BARE_VERDICT_RE = re.compile(r"(PASS|FAIL)\b", re.IGNORECASE)


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
        if "max_rows" in self.config:
            logging.warning(
                "AnalyticsScorer: 'max_rows' is no longer supported and is ignored;"
                " use 'max_data_result_entries' (a rows x columns cell budget)."
            )
        self.max_data_result_entries = int(
            self.config.get(
                "max_data_result_entries", DEFAULT_MAX_DATA_RESULT_ENTRIES
            )
        )
        self.query_label = self.config.get("query_label", "SQL Query")
        # Exact set match implies PASS under the rubric (column names/order and
        # deduplication are tolerated variations 1 and 2), so the LLM call can be
        # skipped. Cortado has no such shortcut; disable for strict parity.
        self.skip_llm_on_exact_match = bool(
            self.config.get("skip_llm_on_exact_match", True)
        )
        self.set_match_checker = setmatcher.SetMatcher({})

    @staticmethod
    def _to_dataframe(data: Any) -> pd.DataFrame | None:
        """Converts an execution result into a DataFrame, or None if not tabular."""
        if isinstance(data, pd.DataFrame):
            return data
        if isinstance(data, dict):
            data = [data]
        if isinstance(data, (list, tuple)):
            try:
                return pd.DataFrame(list(data))
            except (ValueError, TypeError):
                return None
        return None

    def _format_data_result(self, data: Any, data_type: str) -> str:
        """Formats a data result like Cortado's format_dataframe_to_str.

        Returns an empty string when there is no data or the result has zero rows,
        in which case the caller omits the `Data:` block.
        """
        if data is None:
            return ""
        df = self._to_dataframe(data)
        if df is None:
            return str(data).strip()

        original_rows = df.shape[0]
        is_truncated = False
        if df.shape[1] and df.shape[0] * df.shape[1] > self.max_data_result_entries:
            limit = self.max_data_result_entries // df.shape[1]
            df = df.head(limit)
            is_truncated = True

        output = df.to_string(index=False) if not df.empty else ""
        if is_truncated:
            return (
                "(%s dataframe was truncated from %d rows to %d rows for display.) "
                % (data_type, original_rows, df.shape[0])
            ) + output
        return output

    def _render_trajectory(self, query: Any, data: Any, data_type: str) -> str:
        """Renders a single-turn trajectory the way Cortado does for the autorater."""
        parts = []
        if query:
            parts.append(
                _SINGLE_QUERY_TEMPLATE.format(
                    query_label=self.query_label, query=query
                )
            )
        data_str = self._format_data_result(data, data_type)
        if data_str:
            parts.append(_SINGLE_DATA_RESULT_TEMPLATE.format(data=data_str))
        return "\n".join(parts)

    @staticmethod
    def _parse_verdict(response_text: str) -> Tuple[float, str]:
        """Parses the PASS / FAIL verdict from the LLM autorater response.

        The final non-empty line is authoritative; markdown emphasis and code
        formatting (e.g. `**VERDICT:** PASS`) are ignored.
        """
        if not response_text:
            return 0.0, "Could not parse valid VERDICT: empty response from autorater."

        lines = [
            re.sub(r"[*_`#>]", "", line).strip()
            for line in response_text.strip().splitlines()
        ]
        lines = [line for line in lines if line]
        if lines:
            last_line = lines[-1]
            match = _VERDICT_RE.search(last_line) or _BARE_VERDICT_RE.match(
                last_line
            )
            if match:
                verdict = match.group(1).upper()
                return (100.0 if verdict == "PASS" else 0.0), response_text

        # Fall back to the last VERDICT label anywhere in the response.
        matches = _VERDICT_RE.findall(re.sub(r"[*_`]", "", response_text))
        if matches:
            verdict = matches[-1].upper()
            return (100.0 if verdict == "PASS" else 0.0), response_text

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
        """Evaluates trial result against golden reference following the Conversational Analytics rubric.

        Raises:
          ValueError: If the golden reference is unusable (failed or empty). These
            are recorded by score.compare as comparison errors rather than as
            trial failures, mirroring Cortado skipping side-by-side evaluation.
          RuntimeError: If the autorater model fails to return a response.
        """
        # 1. A broken golden reference is not the trial's fault.
        if golden_error:
            raise ValueError(f"Golden query failed to execute: {golden_error}")

        # 2. A failed trial query produces no data result (rubric Check 1), while
        # a successfully executed golden query always does (possibly empty).
        if generated_error:
            return 0.0, f"Generated query failed to execute: {generated_error}"

        # 3. Fast short-circuit: exact set match on non-empty results is a PASS.
        is_empty_results = (not golden_execution_result) and (not generated_execution_result)
        if (
            self.skip_llm_on_exact_match
            and not is_empty_results
            and isinstance(golden_execution_result, list)
            and isinstance(generated_execution_result, list)
            and self._is_exact_match(
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
        ):
            return 100.0, "Skipped. Exact Match was found."

        # 4. Format trajectories.
        golden_trajectory = self._render_trajectory(
            golden_query, golden_execution_result, "golden"
        )
        trial_trajectory = self._render_trajectory(
            generated_query, generated_execution_result, "trial"
        )

        # 5. Empty-trajectory handling, as in Cortado's turn-level autorater.
        if not golden_trajectory:
            raise ValueError(
                "Golden trajectory is empty. Skipping side-by-side rubric evaluation."
            )
        if not trial_trajectory:
            return 0.0, _EMPTY_TRIAL_JUSTIFICATION

        prompt = ANALYTICS_SCORER_PROMPT_TEMPLATE.format(
            rubric=DATA_RESULTS_RUBRIC,
            user_prompt=nl_prompt or "",
            ground_truth_trajectory=golden_trajectory,
            trial_trajectory=trial_trajectory,
        )

        logging.debug("\n --------- Analytics Scorer Prompt: --------- \n %s", prompt)

        # 6. LLM inference. with_cache_execute swallows model exceptions and
        # returns None; surface that as an error instead of a silent FAIL.
        if self.cache_client:
            response = with_cache_execute(
                prompt,
                self.model_config,
                self._inference_without_caching,
                self.cache_client,
            )
            if response is None:
                raise RuntimeError(
                    "AnalyticsScorer autorater returned no response (model call failed)."
                )
        else:
            response = self._inference_without_caching(prompt)

        logging.debug("\n --------- Analytics Scorer Response: --------- \n %s", response)
        return self._parse_verdict(response)
