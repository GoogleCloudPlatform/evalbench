import json
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch, MagicMock
from scorers.goalcompletionrate import GoalCompletionRate

PLAN = "List the instances, then validate state."


def sub_goal(goal, met, reasoning=""):
    return {"goal": goal, "met": met, "reasoning": reasoning}


class ScorerTestCase(unittest.TestCase):
    """Runs GoalCompletionRate against a mocked judge."""

    def score(self, mock_get_generator, response, history="[]",
              include_tool_calls=False, **scenario_fields):
        """Scores one scenario against a mocked judge response.

        `response` is the judge's reply, or an exception for the judge to
        raise. Saves the prompt sent to the judge in self.prompt.
        """
        # A bare MagicMock has every attribute, so it would take JSON mode.
        mock_model = MagicMock(spec=["generate"])
        if isinstance(response, Exception):
            mock_model.generate.side_effect = response
        else:
            mock_model.generate.return_value = response
        mock_get_generator.return_value = mock_model

        scenario = {"conversation_plan": PLAN, **scenario_fields}
        scorer = GoalCompletionRate(
            {"model_config": "fake_config",
             "include_tool_calls": include_tool_calls},
            global_models={})
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
                "conversation_history": history, "scenario": scenario}),
            generated_error="",
        )
        self.prompt = mock_model.generate.call_args[0][0]
        return result


class TestGoalCompletionRate(ScorerTestCase):

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
    def test_judge_prompt_contains_the_plan_and_history(
            self, mock_get_generator):
        """The judge cannot score a conversation it does not see."""
        history = json.dumps([{"user": "list my instances",
                               "agent": "You have 2 instances: a and b."}])

        self.score(mock_get_generator,
                   json.dumps({"sub_goals": [sub_goal("a", True)]}),
                   history=history)

        self.assertIn(PLAN, self.prompt)
        self.assertIn("You have 2 instances: a and b.", self.prompt)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_judge_prompt_is_the_template(self, mock_get_generator):
        from scorers.prompt.goalcompletion import (
            GOAL_COMPLETION_PROMPT, TEXT_ONLY_EVIDENCE_RULE)

        self.score(mock_get_generator, json.dumps(
            {"sub_goals": [sub_goal("a", True)]}))

        self.assertEqual(GOAL_COMPLETION_PROMPT.format(
            conversation_plan=PLAN, conversation_history="[]",
            evidence_rule=TEXT_ONLY_EVIDENCE_RULE), self.prompt)

    @patch('scorers.goalcompletionrate.get_generator')
    def test_evidence_rule_follows_include_tool_calls(self, mock_get_generator):
        """With tool calls in the history, the judge must not trust an
        agent's own report of an action."""
        from scorers.prompt.goalcompletion import (
            TEXT_ONLY_EVIDENCE_RULE, TOOL_CALLS_EVIDENCE_RULE)

        history = json.dumps([{"user": "list my instances", "agent": json.dumps(
            {"response": "Listed them.", "tool_calls": [
                {"tool_name": "cloud-sql__list_instances",
                 "status": "error", "response": "permission denied"}]})}])
        response = json.dumps({"sub_goals": [sub_goal("a", True)]})
        for include_tool_calls, used, unused in [
                (False, TEXT_ONLY_EVIDENCE_RULE, TOOL_CALLS_EVIDENCE_RULE),
                (True, TOOL_CALLS_EVIDENCE_RULE, TEXT_ONLY_EVIDENCE_RULE)]:
            with self.subTest(include_tool_calls=include_tool_calls):
                self.score(mock_get_generator, response, history=history,
                           include_tool_calls=include_tool_calls)

                self.assertIn(used, self.prompt)
                self.assertNotIn(unused, self.prompt)
                self.assertEqual(include_tool_calls,
                                 "permission denied" in self.prompt)

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

    @patch('scorers.goalcompletionrate.get_generator')
    def test_judge_error_scores_0_without_raising(self, mock_get_generator):
        """A judge outage must not stop the other scorers in the run."""
        score, reason = self.score(
            mock_get_generator, RuntimeError("quota exceeded"))

        self.assertEqual(0.0, score)
        self.assertIn("quota exceeded", reason)


class _FakeGeminiJudge:
    """A judge with the genai surface that JSON mode needs."""

    def __init__(self, reply, tools=None, base_prompt=""):
        self.client = object()
        self.base_prompt = base_prompt
        self._genai_tool_config = tools
        self.execs_per_minute = None
        self.semaphore = threading.Semaphore(1)
        self.max_attempts = 3
        self.reply = reply
        self.calls = []
        self.generate = MagicMock(return_value=reply)

    def _call_generate_content(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.reply, candidates=[])


class TestGoalCompletionJsonMode(unittest.TestCase):
    """A Gemini judge answers in JSON mode, which skips sanitize_sql."""

    def run_scorer(self, judge, **scenario_fields):
        scenario = {"conversation_plan": PLAN, **scenario_fields}
        with patch('scorers.goalcompletionrate.get_generator',
                   return_value=judge):
            scorer = GoalCompletionRate(
                {"model_config": "fake_config"}, global_models={})
            return scorer.compare(
                "", "", "", "", "", "", "", "",
                json.dumps({"conversation_history": "[]",
                            "scenario": scenario}),
                "")

    def test_sub_goals_use_json_mode_and_keep_escapes(self):
        """Quotes and backslashes survive, so sanitize_sql did not run."""
        from scorers.prompt.goalcompletion import GOAL_COMPLETION_SCHEMA

        evidence = 'the agent said "done" in C:\\logs'
        judge = _FakeGeminiJudge(json.dumps({
            "sub_goals": [sub_goal("list", True, evidence),
                          sub_goal("validate", False, "never checked")],
            "summary": "",
        }), base_prompt="BASE ")

        score, reason = self.run_scorer(judge)

        self.assertEqual(50.0, score)
        self.assertIn(evidence, reason)
        judge.generate.assert_not_called()
        config = judge.calls[0]["config"]
        self.assertEqual("application/json", config.response_mime_type)
        self.assertEqual(GOAL_COMPLETION_SCHEMA, config.response_schema)
        self.assertTrue(judge.calls[0]["contents"].startswith("BASE "))
        self.assertIn(PLAN, judge.calls[0]["contents"])

    def test_judge_with_tools_uses_generate(self):
        judge = _FakeGeminiJudge(
            json.dumps({"sub_goals": [sub_goal("a", True)]}),
            tools=[object()])

        score, _ = self.run_scorer(judge)

        self.assertEqual(100.0, score)
        judge.generate.assert_called_once()
        self.assertEqual([], judge.calls)

    def test_json_mode_keeps_the_judge_rate_limit(self):
        judge = _FakeGeminiJudge(
            json.dumps({"sub_goals": [sub_goal("a", True)]}))
        judge.execs_per_minute = 30

        with patch('scorers.goalcompletionrate.rate_limit',
                   side_effect=lambda query, fn, *_: fn(*query)) as limit:
            score, _ = self.run_scorer(judge)

        self.assertEqual(100.0, score)
        _, _, execs_per_minute, semaphore, max_attempts = limit.call_args[0]
        self.assertEqual(30, execs_per_minute)
        self.assertIs(judge.semaphore, semaphore)
        self.assertEqual(3, max_attempts)


def summary_row(scores, metric="goal_completion"):
    from reporting.analyzer import analyze_result

    config = {"scorers": {metric: {"model_config": "fake_config"}}}
    _, summary_df = analyze_result(scores, config)
    return summary_df.set_index("metric_name").to_dict(orient="index")[metric]


def score_rows(metric, scores):
    return [
        {"id": str(i), "comparator": metric, "score": score,
         "generated_sql": "SELECT 1", "generated_error": None}
        for i, score in enumerate(scores)
    ]


class TestPartialCreditRollup(unittest.TestCase):

    def test_partial_scores_aggregate_as_partial_credit(self):
        row = summary_row(score_rows("goal_completion", [100.0, 66.7, 0.0]))

        # Exact match: 166.7 / 100 is 1.6669999999999998 without rounding.
        self.assertEqual(1.667, row["correct_results_count"])
        self.assertEqual(3, row["total_results_count"])

    def test_other_metrics_still_count_only_exact_passes(self):
        """Partial credit applies to goal_completion alone."""
        row = summary_row(
            score_rows("trajectory_matcher", [100.0, 66.7, 0.0]),
            metric="trajectory_matcher")

        self.assertEqual(1, row["correct_results_count"])

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

        self.assertEqual(1.5, row["correct_results_count"])
        self.assertEqual(2, row["total_results_count"])

    def test_service_summary_keeps_the_fraction(self):
        """eval_service used to int() the count, truncating 1.67 to 1."""
        from eval_service import _summary_count

        self.assertEqual(1.67, _summary_count(1.667))
        self.assertEqual(3, _summary_count(3.0))


if __name__ == '__main__':
    unittest.main()
