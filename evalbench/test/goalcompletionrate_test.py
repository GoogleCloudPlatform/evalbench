import json
import unittest
from unittest.mock import patch, MagicMock
from scorers.goalcompletionrate import GoalCompletionRate

EVAL_CONTEXT = json.dumps({
    "conversation_history": "[]",
    "scenario": {"conversation_plan": "List the instances, then validate state."},
})


def sub_goal(goal, met, reasoning=""):
    return {"goal": goal, "met": met, "reasoning": reasoning}


class TestGoalCompletionRate(unittest.TestCase):

    def score(self, mock_get_generator, response):
        mock_model = MagicMock()
        mock_model.generate.return_value = response
        mock_get_generator.return_value = mock_model

        scorer = GoalCompletionRate(
            {"model_config": "fake_config"}, global_models={})
        return scorer.compare(
            nl_prompt="",
            golden_query="",
            query_type="",
            golden_execution_result="",
            golden_eval_result="",
            golden_error="",
            generated_query="",
            generated_execution_result="",
            generated_eval_result=EVAL_CONTEXT,
            generated_error="",
        )

    @patch('scorers.goalcompletionrate.get_generator')
    def test_score_is_the_share_of_sub_goals_met(self, mock_get_generator):
        for met, total, expected in [(0, 2, 0.0), (2, 3, 66.7), (2, 2, 100.0)]:
            with self.subTest(met=met, total=total):
                response = json.dumps({
                    "sub_goals": [sub_goal(str(i), i < met)
                                  for i in range(total)],
                })

                score, _ = self.score(mock_get_generator, response)

                self.assertEqual(expected, score)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_explanation_breaks_down_every_sub_goal(self, mock_get_generator):
        response = json.dumps({
            "sub_goals": [
                sub_goal("list instances", True, "the agent listed them"),
                sub_goal("validate RUNNABLE", False, "never checked state"),
            ],
            "summary": "One of two outcomes reached.",
        })

        _, reason = self.score(mock_get_generator, response)

        self.assertIn("1/2 sub-goals met", reason)
        self.assertIn("[MET] list instances", reason)
        self.assertIn("[UNMET] validate RUNNABLE", reason)
        self.assertIn("never checked state", reason)
        self.assertIn("One of two outcomes reached.", reason)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_reasoning_mentioning_pass_does_not_pass(self, mock_get_generator):
        """The verdict comes from the `met` fields, never from the prose."""
        response = json.dumps({
            "sub_goals": [
                sub_goal("create the instance", False,
                         "the agent did not PASS the instance name"),
            ],
            "summary": "The agent failed to PASS any required argument.",
        })

        score, _ = self.score(mock_get_generator, response)

        self.assertEqual(0.0, score)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_fenced_json_is_parsed(self, mock_get_generator):
        body = json.dumps({"sub_goals": [sub_goal("a", True)]})

        score, _ = self.score(
            mock_get_generator, f"```json\n{body}\n```")

        self.assertEqual(100.0, score)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_survives_the_generator_response_sanitizer(self, mock_get_generator):
        """sanitize_sql strips the Markdown fences off a Gemini response before
        the scorer sees it; what is left still has to parse."""
        from util.sanitizer import sanitize_sql

        fenced = "```json\n" + json.dumps({
            "sub_goals": [
                sub_goal("list the instances", True, "the agent listed them"),
                sub_goal("validate the state", False, "the agent never checked"),
            ],
            "summary": "Half the plan was completed.",
        }, indent=2) + "\n```"

        score, _ = self.score(mock_get_generator, sanitize_sql(fenced))

        self.assertEqual(50.0, score)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_string_met_values_are_honored(self, mock_get_generator):
        response = json.dumps({
            "sub_goals": [sub_goal("a", "true"), sub_goal("b", "false")],
        })

        score, _ = self.score(mock_get_generator, response)

        self.assertEqual(50.0, score)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_unparsable_response_scores_0(self, mock_get_generator):
        score, reason = self.score(mock_get_generator, "PASS, looks good")

        self.assertEqual(0.0, score)
        self.assertIn("Invalid JSON", reason)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_response_without_sub_goals_scores_0(self, mock_get_generator):
        score, reason = self.score(
            mock_get_generator, json.dumps({"sub_goals": []}))

        self.assertEqual(0.0, score)
        self.assertIn("No sub-goals", reason)


EXPERIMENT_CONFIG = {
    "scorers": {"goal_completion": {"model_config": "fake_config"}}
}


def summary_row(scores):
    from reporting.analyzer import analyze_result

    _, summary_df = analyze_result(scores, EXPERIMENT_CONFIG)
    return summary_df.set_index("metric_name").to_dict(
        orient="index")["goal_completion"]


class TestPartialCreditRollup(unittest.TestCase):

    def test_partial_scores_aggregate_as_partial_credit(self):
        scores = [
            {"id": str(i), "comparator": "goal_completion", "score": score,
             "generated_sql": "SELECT 1", "generated_error": None}
            for i, score in enumerate([100.0, 66.7, 0.0])
        ]

        row = summary_row(scores)

        self.assertAlmostEqual(1.667, row["correct_results_count"], places=3)
        self.assertEqual(3, row["total_results_count"])

    def test_multi_trial_prompts_are_credited_their_worst_trial(self):
        """Prompt-level rollup keeps the pessimistic min() every other metric
        uses, so a prompt is credited the fraction its weakest trial earned."""
        scores = [
            {"id": f"p{prompt}t{trial}", "prompt_id": f"p{prompt}",
             "comparator": "goal_completion", "score": score,
             "generated_sql": "SELECT 1", "generated_error": None}
            for prompt, trials in enumerate([[100.0, 50.0], [100.0, 100.0]])
            for trial, score in enumerate(trials)
        ]

        row = summary_row(scores)

        self.assertAlmostEqual(1.5, row["correct_results_count"], places=3)
        self.assertEqual(2, row["total_results_count"])

    def test_service_summary_keeps_the_fraction(self):
        """eval_service used to int() the count, truncating 1.67 to 1."""
        from eval_service import _summary_count

        self.assertEqual(1.67, _summary_count(1.667))
        self.assertEqual(3, _summary_count(3.0))


if __name__ == '__main__':
    unittest.main()
