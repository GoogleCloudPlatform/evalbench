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
        """sanitize_sql strips the Markdown fences from a Gemini response
        before the scorer reads it. The rest must still parse."""
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


def criterion_verdict(index, met, reasoning=""):
    return {"index": index, "met": met, "reasoning": reasoning}


class TestGoalCompletionCriteria(unittest.TestCase):
    """The optional `success_criteria` scenario field."""

    def score(self, mock_get_generator, response, **criteria):
        """Scores one scenario against a mocked judge response.

        Saves the prompt sent to the judge in self.prompt.
        """
        mock_model = MagicMock()
        mock_model.generate.return_value = response
        mock_get_generator.return_value = mock_model

        scenario = {"conversation_plan": "List the instances, then validate state."}
        scenario.update(criteria)
        scorer = GoalCompletionRate(
            {"model_config": "fake_config"}, global_models={})
        result = scorer.compare(
            nl_prompt="",
            golden_query="",
            query_type="",
            golden_execution_result="",
            golden_eval_result="",
            golden_error="",
            generated_query="",
            generated_execution_result="",
            generated_eval_result=json.dumps({
                "conversation_history": "[]", "scenario": scenario}),
            generated_error="",
        )
        self.prompt = mock_model.generate.call_args[0][0]
        return result

    @patch('scorers.goalcompletionrate.get_generator')
    def test_scenario_without_criteria_keeps_the_plan_prompt(
            self, mock_get_generator):
        from scorers.prompt.goalcompletion import GOAL_COMPLETION_PROMPT

        self.score(mock_get_generator, json.dumps(
            {"sub_goals": [sub_goal("a", True)]}))

        self.assertEqual(GOAL_COMPLETION_PROMPT.format(
            conversation_plan="List the instances, then validate state.",
            conversation_history="[]"), self.prompt)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_success_criteria_replace_plan_sub_goals(self, mock_get_generator):
        response = json.dumps({
            "success_criteria": [
                criterion_verdict(1, True, "listed them"),
                criterion_verdict(2, False, "never checked"),
                criterion_verdict(3, True, "reported it"),
            ],
        })

        score, reason = self.score(
            mock_get_generator, response,
            success_criteria=["Lists the instances",
                              "Reports the nl2code state",
                              "States the instance count"])

        self.assertEqual(66.7, score)
        self.assertIn("1. Lists the instances", self.prompt)
        self.assertIn("3. States the instance count", self.prompt)
        self.assertNotIn("Break the plan into", self.prompt)
        self.assertIn("2/3 success criteria met", reason)
        self.assertIn("[UNMET] Reports the nl2code state — never checked",
                      reason)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_verdicts_pair_with_criteria_by_index_not_order(
            self, mock_get_generator):
        response = json.dumps({
            "success_criteria": [
                criterion_verdict(2, False, "second"),
                criterion_verdict("1", True, "first"),
            ],
        })

        score, reason = self.score(
            mock_get_generator, response,
            success_criteria=["first criterion", "second criterion"])

        self.assertEqual(50.0, score)
        self.assertIn("[MET] first criterion — first", reason)
        self.assertIn("[UNMET] second criterion — second", reason)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_missing_success_verdict_scores_0(self, mock_get_generator):
        response = json.dumps(
            {"success_criteria": [criterion_verdict(1, True)]})

        score, reason = self.score(
            mock_get_generator, response, success_criteria=["a", "b"])

        self.assertEqual(0.0, score)
        self.assertIn("No verdict for success criteria [2]", reason)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_bare_string_is_one_criterion(self, mock_get_generator):
        response = json.dumps(
            {"success_criteria": [criterion_verdict(1, True)]})

        score, _ = self.score(
            mock_get_generator, response,
            success_criteria="Lists the instances")

        self.assertEqual(100.0, score)
        self.assertIn("1. Lists the instances", self.prompt)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_multi_line_criterion_stays_on_one_prompt_line(
            self, mock_get_generator):
        response = json.dumps(
            {"success_criteria": [criterion_verdict(1, True)]})

        self.score(mock_get_generator, response,
                   success_criteria=["Lists the\n  instances"])

        self.assertIn("1. Lists the instances\n", self.prompt)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_blank_criteria_fall_back_to_plan_sub_goals(
            self, mock_get_generator):
        response = json.dumps({"sub_goals": [sub_goal("a", True)]})

        score, reason = self.score(
            mock_get_generator, response, success_criteria=["", "  ", None])

        self.assertEqual(100.0, score)
        self.assertIn("Break the plan into", self.prompt)
        self.assertIn("1/1 sub-goals met", reason)

    def test_criteria_prompt_survives_the_generator_response_sanitizer(self):
        """The prompt asks for JSON that sanitize_sql leaves intact."""
        from scorers.util import extract_json
        from util.sanitizer import sanitize_sql

        fenced = "```json\n" + json.dumps({
            "success_criteria": [criterion_verdict(1, True, "it listed them")],
            "summary": "Listed the instances.",
        }, indent=2) + "\n```"

        data = extract_json(sanitize_sql(fenced))

        self.assertEqual(1, data["success_criteria"][0]["index"])
        self.assertTrue(data["success_criteria"][0]["met"])


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
        """Prompt-level rollup keeps the min() across trials that every other
        metric uses. A prompt gets the fraction its weakest trial earned."""
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
