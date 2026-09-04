"""AgentScoreWork class."""

from typing import Any
from work.work import Work
from scorers import score as scorer
import threading


class AgentScoreWork(Work):
    """Work class for scoring agent generation results."""

    def __init__(
        self,
        config: dict,
        eval_output: dict,
        scoring_results: list,
        global_models: Any = None,
    ):
        self.config = config
        self.eval_output = eval_output
        self.scoring_results = scoring_results

        if global_models is None:
            global_models = {
                "lock": threading.Lock(),
                "semaphores": {},
                "registered_models": {}
            }
        self.global_models = global_models

    def run(self, work_config: Any = None) -> Any:
        """Runs the agent scoring work.

        Args:
            work_config: Optional configuration for the work.

        Returns:
            The scoring result dictionary.
        """
        scenario = self.eval_output.get("scenario", {})
        metadata = self.eval_output.get("metadata", {})
        golden_sql = self.eval_output.get("golden_sql", "")
        generated_sql = self.eval_output.get("generated_sql", "")
        turn_history = self.eval_output.get("turn_history", [])
        if not golden_sql and turn_history:
            for turn_item in turn_history:
                if turn_item.get("golden_sql"):
                    golden_sql = turn_item["golden_sql"]
                    break
        if not generated_sql and turn_history:
            for turn_item in reversed(turn_history):
                if turn_item.get("generated_sql"):
                    generated_sql = turn_item["generated_sql"]
                    break
        golden_result = self.eval_output.get("golden_result")
        if golden_result is None:
            golden_result = scenario.get("expected_trajectory", [])
        generated_result = self.eval_output.get("generated_result")
        if generated_result is None:
            generated_result = self.eval_output.get("accumulated_tools", [])

        scoring_item = {
            "id": self.eval_output.get("eval_id"),
            "nl_prompt": scenario.get("starting_prompt", ""),
            "golden_sql": golden_sql,
            "query_type": "dql",
            "golden_result": golden_result,
            "golden_eval_results": "",
            "golden_error": self.eval_output.get("golden_error", ""),
            "generated_sql": generated_sql if generated_sql else "skipped",
            "generated_result": generated_result,
            "eval_results": self.eval_output,
            "generated_error": self.eval_output.get("generated_error"),
            "dialects": metadata.get("dialects", []),
            "database": metadata.get("database", "unknown"),
            "job_id": self.eval_output.get("job_id"),
            "turn_history": turn_history,
            "accumulated_tools": self.eval_output.get("accumulated_tools", []),
            "accumulated_skills": self.eval_output.get("accumulated_skills", []),
        }

        scorer.compare(
            eval_output_item=scoring_item,
            experiment_config=self.config,
            scoring_results=self.scoring_results,
            global_models=self.global_models
        )

        # Multi-turn rollup metrics calculation
        if turn_history:
            sql_turns = [t for t in turn_history if t.get("golden_sql") or t.get("generated_sql")]
            if sql_turns:
                set_match_scores = [t.get("set_match", 0.0) for t in sql_turns]
                all_turns_score = 100.0 if all(s == 100.0 for s in set_match_scores) else 0.0
                mean_score = sum(set_match_scores) / len(set_match_scores)

                base_item = {
                    "id": self.eval_output.get("eval_id"),
                    "generated_sql": generated_sql if generated_sql else "skipped",
                    "generated_error": self.eval_output.get("generated_error"),
                    "dialects": metadata.get("dialects", []),
                    "database": metadata.get("database", "unknown"),
                    "job_id": self.eval_output.get("job_id"),
                    "comparison_logs": None,
                    "comparison_error": None,
                }

                # Record multi-turn aggregate metrics
                self.scoring_results.append({
                    **base_item,
                    "comparator": "set_match_all_turns",
                    "score": all_turns_score,
                })
                self.scoring_results.append({
                    **base_item,
                    "comparator": "set_match_mean",
                    "score": mean_score,
                })

                for t_idx, t in enumerate(turn_history):
                    if "set_match" in t:
                        self.scoring_results.append({
                            **base_item,
                            "comparator": f"set_match_turn_{t_idx + 1}",
                            "score": float(t["set_match"]),
                        })

        return self.eval_output
