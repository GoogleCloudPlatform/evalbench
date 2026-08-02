"""Unit tests for the dataset-quality scoring flow.

Covers the pieces whose contracts the rest of the flow depends on: the grading
rollup (``grading.py``), the judge-response parsing helpers (``llm.py``), the two
static sub-scorers, and the orchestrator's assembly of score rows
(``scorer.py``).

Not yet covered: the five LLM judge sub-scorers, ``synthesis.py``, ``render.py``,
``context.py``, the ``dataset-quality-format`` loader, and the viewer. The
Gemini JSON-mode branch of ``llm.generate_json`` is also untested; every judge
call here takes the ``model.generate`` fallback.
"""

import json
import os
import sys
import unittest
from unittest.mock import patch

from mcp import types as mcp_types

# Make the ``scorers`` package importable when the test is run directly.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.mcp_client import McpToolsError
from scorers.dataset_quality import llm
from scorers.dataset_quality.context import (
    CATEGORY_DISCOVERABILITY,
    DatasetQualityContext,
    SubScoreContribution,
    SubScorer,
)
from scorers.dataset_quality.grading import (
    ScoredMetric,
    compute_grade,
    fraction_score,
    letter_grade,
)
from scorers.dataset_quality.naming_distribution import NamingDistributionScorer
from scorers.dataset_quality.scorer import SCORER_REGISTRY, DatasetQualityScorer
from scorers.dataset_quality.trajectory_coverage import TrajectoryCoverageScorer


class _StubModel:
    """Judge stand-in returning a canned response.

    Deliberately not a ``MagicMock``: ``llm.generate_json`` branches on the
    model having ``client`` and ``_call_generate_content``, which a mock
    auto-creates, silently routing the call down the Gemini JSON-mode path.
    """

    def __init__(self, response):
        self.response = response

    def generate(self, prompt):
        return self.response


def _tool(name, properties=None):
    return mcp_types.Tool(
        name=name,
        description=f"{name} description",
        inputSchema={"properties": properties or {}},
    )


def _context(scenarios, tools):
    return DatasetQualityContext(
        product_name="widget", scenarios=scenarios, tools=tools
    )


def _metric(name, weight, category, score, applicable=True):
    return ScoredMetric(
        name=name,
        weight=weight,
        category=category,
        score=score,
        applicable=applicable,
    )


class GradingTest(unittest.TestCase):

    def test_letter_grade_band_boundaries(self):
        self.assertEqual(letter_grade(100), "A")
        self.assertEqual(letter_grade(90), "A")
        self.assertEqual(letter_grade(89), "B")
        self.assertEqual(letter_grade(75), "B")
        self.assertEqual(letter_grade(74), "C")
        self.assertEqual(letter_grade(60), "C")
        self.assertEqual(letter_grade(59), "D")
        self.assertEqual(letter_grade(40), "D")
        self.assertEqual(letter_grade(39), "F")
        self.assertEqual(letter_grade(0), "F")

    def test_fraction_score_caps_at_target(self):
        self.assertEqual(fraction_score(2, 10, 0.5), 40)
        self.assertEqual(fraction_score(5, 10, 0.5), 100)
        self.assertEqual(fraction_score(8, 10, 0.5), 100)

    def test_fraction_score_without_a_denominator_is_zero(self):
        self.assertEqual(fraction_score(0, 0, 0.5), 0)
        self.assertEqual(fraction_score(3, 10, 0), 0)

    def test_inapplicable_metric_is_excluded_from_the_weighted_average(self):
        grade = compute_grade([
            _metric("a", 20, "cat_a", 100),
            _metric("b", 30, "cat_a", 50),
            _metric("c", 50, "cat_b", None, applicable=False),
        ])

        self.assertEqual(grade["dataset_quality_score"], 70)
        self.assertEqual(grade["letter_grade"], "C")
        self.assertEqual(grade["graded_weight"], 50)
        self.assertEqual(grade["total_weight"], 100)
        self.assertEqual(grade["excluded_scorers"], ["c"])

    def test_inapplicable_metric_is_excluded_even_when_it_carries_a_score(self):
        grade = compute_grade([
            _metric("a", 20, "cat_a", 100),
            _metric("b", 20, "cat_a", 0, applicable=False),
        ])

        self.assertEqual(grade["dataset_quality_score"], 100)
        self.assertEqual(grade["graded_weight"], 20)
        self.assertEqual(grade["excluded_scorers"], ["b"])

    def test_applicable_metric_without_a_score_is_excluded(self):
        grade = compute_grade([
            _metric("a", 20, "cat_a", 100),
            _metric("b", 30, "cat_a", None),
        ])

        self.assertEqual(grade["dataset_quality_score"], 100)
        self.assertEqual(grade["graded_weight"], 20)
        self.assertEqual(grade["excluded_scorers"], ["b"])

    def test_nothing_gradeable_yields_null_not_zero(self):
        grade = compute_grade([
            _metric("a", 20, "cat_a", None, applicable=False),
            _metric("b", 30, "cat_b", None, applicable=False),
        ])

        self.assertIsNone(grade["dataset_quality_score"])
        self.assertIsNone(grade["letter_grade"])
        self.assertEqual(grade["category_scores"], {})
        self.assertEqual(grade["graded_weight"], 0)
        self.assertEqual(grade["total_weight"], 50)

    def test_zero_weight_metrics_are_ungradeable_but_not_excluded(self):
        grade = compute_grade([_metric("a", 0, "cat_a", 100)])

        self.assertIsNone(grade["dataset_quality_score"])
        self.assertIsNone(grade["letter_grade"])
        self.assertEqual(grade["excluded_scorers"], [])

    def test_category_scores_are_weight_normalized_within_the_category(self):
        grade = compute_grade([
            _metric("a", 10, "cat_a", 100),
            _metric("b", 30, "cat_a", 0),
            _metric("c", 60, "cat_b", 50),
        ])

        self.assertEqual(grade["category_scores"], {"cat_a": 25, "cat_b": 50})
        self.assertEqual(grade["dataset_quality_score"], 40)
        self.assertEqual(grade["letter_grade"], "D")

    def test_score_is_rounded_before_it_is_graded(self):
        grade = compute_grade([_metric("a", 1, "cat_a", 59.7)])

        # An unrounded 59.7 would band to D while still being reported as 60.
        self.assertEqual(grade["dataset_quality_score"], 60)
        self.assertEqual(grade["letter_grade"], "C")


class GroupIdsTest(unittest.TestCase):

    def test_ids_follow_dataset_order_and_are_deduped(self):
        grouped = llm.group_ids(
            {"a_ids": ["c3", "c1", "c3"]}, ("a_ids",), ["c1", "c2", "c3"]
        )

        self.assertEqual(grouped, {"a_ids": ["c1", "c3"]})

    def test_ids_absent_from_the_dataset_are_dropped(self):
        grouped = llm.group_ids(
            {"a_ids": ["c1", "hallucinated"]}, ("a_ids",), ["c1", "c2"]
        )

        self.assertEqual(grouped, {"a_ids": ["c1"]})

    def test_missing_or_non_list_labels_become_empty_lists(self):
        grouped = llm.group_ids(
            {"a_ids": "c1"}, ("a_ids", "b_ids"), ["c1", "c2"]
        )

        self.assertEqual(grouped, {"a_ids": [], "b_ids": []})


class JudgeParsingTest(unittest.TestCase):

    def test_labeled_json_passes_through_keys_beyond_the_labels(self):
        model = _StubModel(json.dumps({
            "a_ids": ["c1"],
            "recommendations": ["Add a CUJ."],
        }))

        data = llm.judge_labeled_json(model, "prompt", {}, ("a_ids",))

        self.assertEqual(data["a_ids"], ["c1"])
        self.assertEqual(data["recommendations"], ["Add a CUJ."])

    def test_labeled_json_reads_through_a_code_fence(self):
        model = _StubModel('```json\n{"a_ids": ["c1"]}\n```')

        data = llm.judge_labeled_json(model, "prompt", {}, ("a_ids",))

        self.assertEqual(data, {"a_ids": ["c1"]})

    def test_labeled_json_returns_none_when_no_label_list_is_present(self):
        model = _StubModel(json.dumps({"recommendations": ["something"]}))

        self.assertIsNone(
            llm.judge_labeled_json(model, "prompt", {}, ("a_ids",))
        )

    def test_labeled_json_returns_none_on_an_unparseable_response(self):
        self.assertIsNone(
            llm.judge_labeled_json(_StubModel(""), "prompt", {}, ("a_ids",))
        )
        self.assertIsNone(
            llm.judge_labeled_json(_StubModel("not json"), "prompt", {}, ("a_ids",))
        )

    def test_coverage_drops_non_dict_entries(self):
        model = _StubModel(json.dumps({
            "coverage": [{"tool": "alpha", "parameter": "p"}, "junk"],
        }))

        data = llm.judge_coverage(model, "prompt")

        self.assertEqual(data["coverage"], [{"tool": "alpha", "parameter": "p"}])

    def test_coverage_returns_none_without_a_coverage_list(self):
        model = _StubModel(json.dumps({"coverage": {"tool": "alpha"}}))

        self.assertIsNone(llm.judge_coverage(model, "prompt"))

    def test_example_prompts_collapse_whitespace_and_dedupe(self):
        data = {"recommendations": ["List  the\n  files", "List the files", 7]}

        self.assertEqual(
            llm.example_prompts(data, "recommendations"), ["List the files"]
        )

    def test_example_prompts_missing_key_is_empty(self):
        self.assertEqual(llm.example_prompts({}, "recommendations"), [])


class TrajectoryCoverageScorerTest(unittest.TestCase):

    def test_no_schema_tools_is_inapplicable(self):
        contribution = TrajectoryCoverageScorer({}, {}).run(
            _context([{"id": "c1", "expected_trajectory": ["alpha"]}], [])
        )

        self.assertFalse(contribution.applicable)

    def test_tools_outside_the_schema_do_not_count_as_coverage(self):
        context = _context(
            [{"id": "c1", "expected_trajectory": ["alpha", "retired_tool"]}],
            [_tool("alpha"), _tool("beta"), _tool("gamma"), _tool("delta")],
        )

        contribution = TrajectoryCoverageScorer({}, {}).run(context)

        self.assertEqual(contribution.score, 25)
        self.assertEqual(contribution.metrics["dq_covered_tools"], 1)
        self.assertEqual(contribution.metrics["dq_total_tools"], 4)
        self.assertIn("beta, delta, gamma", contribution.suggestions[0])

    def test_full_coverage_scores_100_without_suggestions(self):
        context = _context(
            [
                {"id": "c1", "expected_trajectory": ["alpha"]},
                {"id": "c2", "expected_trajectory": ["beta"]},
            ],
            [_tool("alpha"), _tool("beta")],
        )

        contribution = TrajectoryCoverageScorer({}, {}).run(context)

        self.assertEqual(contribution.score, 100)
        self.assertEqual(contribution.suggestions, [])


class NamingDistributionScorerTest(unittest.TestCase):

    def _run(self, scenarios, tools=("mcp__search_files", "ls")):
        context = _context(scenarios, [_tool(name) for name in tools])
        return NamingDistributionScorer({}, {}).run(context)

    def _prompts(self, *prompts):
        return [
            {"id": f"c{i}", "starting_prompt": prompt}
            for i, prompt in enumerate(prompts)
        ]

    def test_empty_dataset_is_inapplicable(self):
        self.assertFalse(self._run([]).applicable)

    def test_no_tool_names_is_inapplicable(self):
        self.assertFalse(
            self._run([{"id": "c1", "starting_prompt": "do it"}], tools=()).applicable
        )

    def test_tool_names_are_matched_on_word_boundaries(self):
        contribution = self._run(
            self._prompts("That claim is false", "Use ls to see what is here")
        )

        self.assertEqual(contribution.evidence["names_tool_ids"], ["c1"])
        self.assertEqual(contribution.evidence["intent_based_ids"], ["c0"])

    def test_every_surface_form_of_a_tool_name_matches(self):
        contribution = self._run(self._prompts(
            "Call mcp__search_files for me",
            "Call search_files for me",
            "Run search files on the repo",
            "Find every config in the repo",
        ))

        self.assertEqual(
            contribution.evidence["names_tool_ids"], ["c0", "c1", "c2"]
        )
        self.assertEqual(contribution.evidence["intent_based_ids"], ["c3"])

    def test_indirect_share_is_the_score(self):
        contribution = self._run(self._prompts(
            "Use ls here", "Find every config", "Find every secret"
        ))

        self.assertEqual(contribution.score, 67)
        self.assertEqual(contribution.metrics["dq_tool_named_count"], 1)

    def test_only_the_starting_prompt_is_inspected(self):
        contribution = self._run([{
            "id": "c1",
            "starting_prompt": "Find every config in the repo",
            "conversation_plan": "The agent should call search_files.",
        }])

        self.assertEqual(contribution.score, 100)
        self.assertEqual(contribution.evidence["names_tool_ids"], [])

    def test_no_suggestion_at_or_below_the_target_share(self):
        at_target = self._run(
            self._prompts("Use ls here", *["Find every config"] * 9)
        )

        self.assertEqual(at_target.metrics["dq_tool_named_count"], 1)
        self.assertEqual(at_target.suggestions, [])

    def test_suggestion_above_the_target_share(self):
        above_target = self._run(
            self._prompts("Use ls here", "Use ls there", *["Find every config"] * 8)
        )

        self.assertEqual(len(above_target.suggestions), 1)
        self.assertIn("2/10", above_target.suggestions[0])


def _wrapper(cujs):
    return json.dumps({"scenario": {"all_cujs": cujs}})


def _compare(scorer, generated_eval_result):
    """Convenience wrapper around the comparator's wide ``compare`` API."""
    return scorer.compare(
        nl_prompt="",
        golden_query="",
        query_type="",
        golden_execution_result="",
        golden_eval_result="",
        golden_error="",
        generated_query="",
        generated_execution_result="",
        generated_eval_result=generated_eval_result,
        generated_error="",
    )


def _model_config():
    return {"setup": {"mcp_servers": {"widget": {"httpUrl": "https://x"}}}}


def _config(**overrides):
    config = {
        "product_name": "widget",
        "model_config": "model.yaml",
        "sub_scorers": {"trajectory_coverage": {}},
    }
    config.update(overrides)
    return config


class _FakeDiscoverabilityScorer(SubScorer):
    """Second scorer in an existing category, to exercise the category merge."""

    name = "fake_discoverability"
    category = CATEGORY_DISCOVERABILITY
    default_weight = 30

    def run(self, context):
        return SubScoreContribution(score=0, suggestions=["fake gap"])


class DatasetQualityScorerConfigTest(unittest.TestCase):

    def test_product_name_is_required(self):
        with self.assertRaises(ValueError):
            DatasetQualityScorer(_config(product_name=None), {})

    def test_model_config_is_required(self):
        with self.assertRaises(ValueError):
            DatasetQualityScorer(_config(model_config=None), {})

    def test_sub_scorers_block_is_required(self):
        with self.assertRaises(ValueError):
            DatasetQualityScorer(_config(sub_scorers={}), {})

    def test_unknown_sub_scorer_is_rejected(self):
        with self.assertRaises(ValueError) as ctx:
            DatasetQualityScorer(_config(sub_scorers={"nope": {}}), {})

        self.assertIn("nope", str(ctx.exception))

    def test_sub_scorer_weight_overrides_the_default(self):
        scorer = DatasetQualityScorer(
            _config(sub_scorers={"trajectory_coverage": {"weight": 42}}), {}
        )

        self.assertEqual(scorer.scorers[0].weight, 42)

    def test_registry_default_weights_are_a_budget_of_100(self):
        # graded_weight/total_weight are only readable as a percentage of the
        # rubric because the defaults sum to 100.
        total = sum(cls.default_weight for cls in SCORER_REGISTRY.values())

        self.assertEqual(total, 100)


class ExtractCujsTest(unittest.TestCase):

    def setUp(self):
        self.scorer = DatasetQualityScorer(_config(), {})

    def test_reads_cujs_from_a_json_string(self):
        cujs = self.scorer._extract_cujs(_wrapper([{"id": "c1"}]))

        self.assertEqual(cujs, [{"id": "c1"}])

    def test_reads_cujs_from_a_dict(self):
        cujs = self.scorer._extract_cujs({"scenario": {"all_cujs": [{"id": "c1"}]}})

        self.assertEqual(cujs, [{"id": "c1"}])

    def test_malformed_or_missing_payloads_yield_no_cujs(self):
        self.assertEqual(self.scorer._extract_cujs("not json"), [])
        self.assertEqual(self.scorer._extract_cujs(""), [])
        self.assertEqual(self.scorer._extract_cujs(None), [])
        self.assertEqual(self.scorer._extract_cujs({"scenario": {}}), [])


class FetchToolsTest(unittest.TestCase):

    def setUp(self):
        self.scorer = DatasetQualityScorer(_config(), {})

    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_model_config_without_mcp_servers_raises(self, mock_load):
        mock_load.return_value = {"setup": {}}

        with self.assertRaises(McpToolsError):
            self.scorer._fetch_tools()

    @patch("scorers.dataset_quality.scorer.time.sleep")
    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_a_transient_failure_is_retried(self, mock_load, mock_fetch, _sleep):
        mock_load.return_value = _model_config()
        mock_fetch.side_effect = [McpToolsError("blip"), [_tool("alpha")]]

        self.assertEqual(self.scorer._fetch_tools(), [_tool("alpha")])
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("scorers.dataset_quality.scorer.time.sleep")
    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_repeated_failures_surface_after_the_last_attempt(
        self, mock_load, mock_fetch, _sleep
    ):
        mock_load.return_value = _model_config()
        mock_fetch.side_effect = McpToolsError("down")

        with self.assertRaises(McpToolsError):
            self.scorer._fetch_tools()
        self.assertEqual(mock_fetch.call_count, 3)

    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_an_empty_catalog_is_not_retried(self, mock_load, mock_fetch):
        mock_load.return_value = _model_config()
        mock_fetch.return_value = []

        with self.assertRaises(McpToolsError):
            self.scorer._fetch_tools()
        self.assertEqual(mock_fetch.call_count, 1)


class DatasetQualityScorerCompareTest(unittest.TestCase):

    def test_an_empty_dataset_scores_null_rather_than_zero(self):
        scorer = DatasetQualityScorer(_config(), {})

        rows = _compare(scorer, _wrapper([]))

        self.assertEqual(len(rows), 1)
        name, score, reason = rows[0]
        self.assertEqual(name, "dataset_quality")
        self.assertIsNone(score)
        self.assertFalse(json.loads(reason)["graded"])

    @patch("scorers.dataset_quality.scorer.time.sleep")
    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_tool_discovery_failure_scores_null_rather_than_zero(
        self, mock_load, mock_fetch, _sleep
    ):
        mock_load.return_value = _model_config()
        mock_fetch.side_effect = McpToolsError("down")
        scorer = DatasetQualityScorer(_config(), {})

        rows = _compare(scorer, _wrapper([{"id": "c1"}]))

        self.assertEqual(len(rows), 1)
        name, score, reason = rows[0]
        self.assertEqual(name, "dataset_quality")
        self.assertIsNone(score)
        self.assertIn("tool discovery failed", json.loads(reason)["error"])

    @patch.object(
        TrajectoryCoverageScorer,
        "run",
        return_value=SubScoreContribution(applicable=False),
    )
    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_every_scorer_dropping_out_scores_null_rather_than_zero(
        self, mock_load, mock_fetch, _run
    ):
        mock_load.return_value = _model_config()
        mock_fetch.return_value = [_tool("alpha")]
        scorer = DatasetQualityScorer(_config(), {})

        rows = _compare(scorer, _wrapper([{"id": "c1"}]))

        self.assertEqual([score for _, score, _ in rows], [None, None])
        summary = json.loads(rows[0][2])
        self.assertIsNone(summary["letter_grade"])
        self.assertEqual(summary["excluded_scorers"], ["trajectory_coverage"])

    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_a_summary_row_is_emitted_alongside_one_row_per_category(
        self, mock_load, mock_fetch
    ):
        mock_load.return_value = _model_config()
        mock_fetch.return_value = [_tool("alpha"), _tool("beta")]
        scorer = DatasetQualityScorer(_config(), {})

        rows = _compare(
            scorer, _wrapper([{"id": "c1", "expected_trajectory": ["alpha"]}])
        )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0], "dataset_quality")
        self.assertEqual(rows[0][1], 50.0)

        summary = json.loads(rows[0][2])
        self.assertEqual(summary["letter_grade"], "D")
        self.assertEqual(summary["total_cujs"], 1)
        self.assertNotIn("categories", summary)

        self.assertEqual(rows[1][0], "tool_activation_faithfulness")
        self.assertEqual(rows[1][1], 50.0)
        self.assertEqual(
            json.loads(rows[1][2])["sub_scores"], {"trajectory_coverage": 50}
        )

    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_scores_are_floats_for_the_shared_scores_column(
        self, mock_load, mock_fetch
    ):
        mock_load.return_value = _model_config()
        mock_fetch.return_value = [_tool("alpha")]
        scorer = DatasetQualityScorer(_config(), {})

        rows = _compare(
            scorer, _wrapper([{"id": "c1", "expected_trajectory": ["alpha"]}])
        )

        for _, score, _ in rows:
            self.assertIsInstance(score, float)

    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_scorers_sharing_a_category_merge_into_one_row(
        self, mock_load, mock_fetch
    ):
        mock_load.return_value = _model_config()
        mock_fetch.return_value = [_tool("alpha")]
        registry = dict(
            SCORER_REGISTRY, fake_discoverability=_FakeDiscoverabilityScorer
        )
        with patch("scorers.dataset_quality.scorer.SCORER_REGISTRY", registry):
            scorer = DatasetQualityScorer(
                _config(sub_scorers={
                    "naming_distribution": {"weight": 10},
                    "fake_discoverability": {"weight": 30},
                }),
                {},
            )
            rows = _compare(
                scorer,
                _wrapper([{"id": "c1", "starting_prompt": "Find every config"}]),
            )

        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[1][0], "discoverability_coverage")
        self.assertEqual(rows[1][1], 25.0)

        category = json.loads(rows[1][2])
        self.assertEqual(
            category["sub_scores"],
            {"naming_distribution": 100, "fake_discoverability": 0},
        )
        self.assertEqual(category["gaps"], ["fake gap"])

    @patch.object(NamingDistributionScorer, "run", side_effect=RuntimeError("boom"))
    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_a_raising_sub_scorer_drops_out_without_taking_the_run_down(
        self, mock_load, mock_fetch, _run
    ):
        mock_load.return_value = _model_config()
        mock_fetch.return_value = [_tool("alpha")]
        scorer = DatasetQualityScorer(
            _config(sub_scorers={
                "trajectory_coverage": {"weight": 20},
                "naming_distribution": {"weight": 5},
            }),
            {},
        )

        rows = _compare(
            scorer, _wrapper([{"id": "c1", "expected_trajectory": ["alpha"]}])
        )

        summary = json.loads(rows[0][2])
        self.assertEqual(summary["dataset_quality_score"], 100)
        self.assertEqual(summary["excluded_scorers"], ["naming_distribution"])
        self.assertEqual(summary["graded_weight"], 20)
        self.assertEqual(summary["total_weight"], 25)


if __name__ == "__main__":
    unittest.main()
