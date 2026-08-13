"""Unit tests for the dataset-quality scoring flow.

Covers the pieces whose contracts the rest of the flow depends on: the grading
rollup (``grading.py``), the judge-response parsing helpers (``llm.py``), the
static sub-scorers and the skill catalog they grade against
(``skills_catalog.py``), and the orchestrator's assembly of score rows
(``scorer.py``).

Not yet covered: the five LLM judge sub-scorers, ``synthesis.py``, ``render.py``,
``context.py``, the ``dataset-quality-format`` loader, and the viewer. The
Gemini JSON-mode branch of ``llm.generate_json`` is also untested; every judge
call here takes the ``model.generate`` fallback.
"""

import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from mcp import types as mcp_types

# Make the ``scorers`` package importable when the test is run directly.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.mcp_client import McpToolsError
from generators.models.skills_catalog import (
    Skill,
    SkillCatalogError,
    resolve_skills,
)
from scorers.dataset_quality import llm
from scorers.dataset_quality.composition import CompositionScorer
from scorers.dataset_quality.context import (
    CATEGORY_DISCOVERABILITY,
    CATEGORY_TOOL_ACTIVATION,
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
from scorers.dataset_quality.prompts.composition_coverage import (
    KEY_MULTI_TOOL,
    KEY_SEQUENCE_DEPENDENCY,
)
from scorers.dataset_quality.scorer import SCORER_REGISTRY, DatasetQualityScorer
from scorers.dataset_quality.trajectory_coverage import TrajectoryCoverageScorer


_TRAJECTORY_WEIGHT = TrajectoryCoverageScorer.default_weight


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


def _context(scenarios, tools, skills=()):
    return DatasetQualityContext(
        product_name="widget",
        scenarios=scenarios,
        tools=tools,
        skills=list(skills),
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

    def test_example_prompts_ignores_a_bare_string(self):
        # Iterating a str yields one prompt per character.
        self.assertEqual(
            llm.example_prompts({"recommendations": "abc"}, "recommendations"), []
        )

    def test_an_array_wrapping_one_object_is_unwrapped(self):
        model = _StubModel(json.dumps([{"a_ids": ["c1"]}]))

        data = llm.judge_labeled_json(model, "prompt", None, ["a_ids"])

        self.assertEqual(data, {"a_ids": ["c1"]})

    def test_judges_return_none_on_an_objectless_array(self):
        # json.loads accepts an array, so the callers used to hit .get() on a list.
        model = _StubModel(json.dumps(["c1", "c2"]))

        self.assertIsNone(
            llm.judge_labeled_json(model, "prompt", None, ["a_ids"])
        )
        self.assertIsNone(llm.judge_coverage(model, "prompt"))


class TrajectoryCoverageScorerTest(unittest.TestCase):

    def test_no_tools_and_no_scripts_is_inapplicable(self):
        contribution = TrajectoryCoverageScorer({}, {}).run(
            _context([{"id": "c1", "expected_trajectory": ["alpha"]}], [])
        )

        self.assertFalse(contribution.applicable)

    def test_a_skills_only_product_is_scored_against_its_scripts(self):
        # A skill groups operations rather than being one, so a trajectory names
        # the scripts; without this the whole field goes ungraded.
        context = _context(
            [{"id": "c1", "expected_trajectory": ["list_instances.js"]}],
            [],
            [
                Skill("admin", scripts=("create_instance.js", "list_instances.js")),
                Skill("data", scripts=("execute_sql.js", "list_instances.js")),
            ],
        )

        contribution = TrajectoryCoverageScorer({}, {}).run(context)

        # list_instances.js ships in both skills but is one operation, so the
        # catalog is 3 operations plus the 2 skills.
        self.assertEqual(contribution.metrics["capabilities_total"], 5)
        self.assertEqual(contribution.metrics["capabilities_covered"], 1)
        self.assertEqual(contribution.score, 20)
        self.assertIn("create_instance.js", contribution.suggestions[0])

    def test_a_products_tools_and_skills_are_one_catalog(self):
        # Both channels are installed together, so grading only the tools scores
        # a skills-authored dataset against a surface it never names.
        context = _context(
            [{"id": "c1", "expected_trajectory": ["alpha"]}],
            [_tool("alpha")],
            [Skill("admin", scripts=("unrelated.js",))],
        )

        contribution = TrajectoryCoverageScorer({}, {}).run(context)

        self.assertEqual(contribution.metrics["capabilities_total"], 3)
        self.assertEqual(contribution.score, 33)

    def test_a_skill_and_its_scripts_are_both_covered(self):
        context = _context(
            [{
                "id": "c1",
                "expected_trajectory": ["list_instances.js"],
                "expected_skills": ["admin"],
            }],
            [],
            [Skill("admin", scripts=("list_instances.js",))],
        )

        contribution = TrajectoryCoverageScorer({}, {}).run(context)

        self.assertEqual(contribution.score, 100)
        self.assertEqual(contribution.suggestions, [])

    def test_tools_outside_the_schema_do_not_count_as_coverage(self):
        context = _context(
            [{"id": "c1", "expected_trajectory": ["alpha", "retired_tool"]}],
            [_tool("alpha"), _tool("beta"), _tool("gamma"), _tool("delta")],
        )

        contribution = TrajectoryCoverageScorer({}, {}).run(context)

        self.assertEqual(contribution.score, 25)
        self.assertEqual(contribution.metrics["capabilities_covered"], 1)
        self.assertEqual(contribution.metrics["capabilities_total"], 4)
        self.assertIn("beta, delta, gamma", contribution.suggestions[0])

    def test_a_string_trajectory_is_ignored_rather_than_split(self):
        # Iterating a str registers each letter as a covered tool name, so a
        # single-character tool is spuriously credited.
        context = _context(
            [{"id": "c1", "expected_trajectory": "alpha"}],
            [_tool("a"), _tool("alpha")],
        )

        contribution = TrajectoryCoverageScorer({}, {}).run(context)

        self.assertEqual(contribution.score, 0)
        self.assertEqual(context.exercised_tools(), [])

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


class ResolveSkillsTest(unittest.TestCase):
    """The skill catalog trajectory_coverage scores against, read from setup."""

    def setUp(self):
        self.root = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.root, True)

    def _skill(self, parent, dir_name, frontmatter="", body="body", scripts=()):
        path = os.path.join(parent, dir_name)
        os.makedirs(path, exist_ok=True)
        with open(os.path.join(path, "SKILL.md"), "w", encoding="utf-8") as f:
            f.write(f"{frontmatter}{body}\n")
        if scripts:
            os.makedirs(os.path.join(path, "scripts"), exist_ok=True)
            for script in scripts:
                open(os.path.join(path, "scripts", script), "w").close()
        return path

    @staticmethod
    def _frontmatter(name, description):
        return f"---\nname: {name}\ndescription: {description}\n---\n"

    def test_a_directory_holding_a_skill_md_is_one_skill(self):
        self._skill(self.root, "solo", self._frontmatter("solo", "does solo"))

        skills = resolve_skills(
            {"skills": [{"action": "link", "path": os.path.join(self.root, "solo")}]}
        )

        self.assertEqual(skills, [Skill("solo", "does solo")])

    def test_a_skills_subdirectory_wins_over_the_root_children(self):
        nested = os.path.join(self.root, "skills")
        self._skill(nested, "alpha")
        self._skill(self.root, "docs")

        skills = resolve_skills({"skills": [self.root]})

        self.assertEqual([s.name for s in skills], ["alpha"])

    def test_direct_children_are_scanned_without_a_skills_subdirectory(self):
        self._skill(self.root, "alpha")
        self._skill(self.root, "beta")
        os.makedirs(os.path.join(self.root, "not-a-skill"))

        skills = resolve_skills({"skills_dir": self.root})

        self.assertEqual([s.name for s in skills], ["alpha", "beta"])

    def test_frontmatter_name_wins_over_the_directory_name(self):
        self._skill(
            self.root, "dir-name", self._frontmatter("declared-name", "d")
        )

        skills = resolve_skills({"skills_dir": self.root})

        self.assertEqual(skills, [Skill("declared-name", "d")])

    def test_absent_or_malformed_frontmatter_falls_back_to_the_directory(self):
        self._skill(self.root, "bare")
        self._skill(self.root, "broken", "---\nname: [unclosed\n---\n")

        skills = resolve_skills({"skills_dir": self.root})

        self.assertEqual(skills, [Skill("bare", ""), Skill("broken", "")])

    def test_an_entry_installing_a_subset_is_not_scored_against_the_rest(self):
        for name in ("alpha", "beta", "gamma"):
            self._skill(self.root, name)

        skills = resolve_skills(
            {"skills": [{"path": self.root, "skills": ["alpha", "gamma"]}]}
        )

        self.assertEqual([s.name for s in skills], ["alpha", "gamma"])

    def test_a_skills_scripts_are_read_as_its_operations(self):
        self._skill(
            self.root, "admin", scripts=("create_instance.js", "list_instances.js")
        )

        skills = resolve_skills({"skills_dir": self.root})

        self.assertEqual(
            skills[0].scripts, ("create_instance.js", "list_instances.js")
        )

    def test_a_skill_without_a_scripts_directory_has_no_operations(self):
        self._skill(self.root, "admin")

        skills = resolve_skills({"skills_dir": self.root})

        self.assertEqual(skills[0].scripts, ())

    def test_an_undeclared_catalog_resolves_to_nothing(self):
        self.assertEqual(resolve_skills({}), [])

    def test_a_missing_path_is_fatal_rather_than_a_smaller_catalog(self):
        # Skipping it would shrink the denominator and inflate coverage.
        with self.assertRaises(SkillCatalogError):
            resolve_skills({"skills_dir": os.path.join(self.root, "absent")})

    def test_a_narrowing_key_matching_nothing_is_fatal(self):
        self._skill(self.root, "alpha")

        with self.assertRaises(SkillCatalogError):
            resolve_skills({"skills": [{"path": self.root, "skill": "stale"}]})

    def test_a_source_holding_no_skill_md_is_fatal(self):
        os.makedirs(os.path.join(self.root, "docs"))

        with self.assertRaises(SkillCatalogError):
            resolve_skills({"skills_dir": self.root})

    def test_one_broken_entry_is_fatal_even_when_another_resolves(self):
        # Checking only the combined total would pass here, leaving the catalog
        # short of the product's real surface and inflating coverage.
        working = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, working, True)
        self._skill(working, "alpha")

        with self.assertRaises(SkillCatalogError):
            resolve_skills({"skills": [working, os.path.join(self.root, "absent")]})

    def test_an_entry_naming_no_source_is_fatal(self):
        # Only a path or url resolves to a catalog; a bare name would have to be
        # trusted rather than read, and no config declares skills that way.
        for entry in ({"action": "install"}, {"action": "enable", "name": "a"}):
            with self.subTest(entry=entry):
                with self.assertRaises(SkillCatalogError):
                    resolve_skills({"skills": [entry]})

    def test_the_same_skill_from_two_sources_is_listed_once(self):
        self._skill(self.root, "alpha", self._frontmatter("Alpha", "first"))
        other = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, other, True)
        self._skill(other, "alpha", self._frontmatter("alpha", "second"))

        skills = resolve_skills({"skills": [self.root, other]})

        self.assertEqual(skills, [Skill("Alpha", "first")])

    @patch("generators.models.skills_catalog.subprocess.run")
    def test_a_git_url_is_cloned_and_scanned(self, mock_run):
        def clone(cmd, **kwargs):
            self._skill(cmd[-1], "alpha", self._frontmatter("alpha", "cloned"))

        mock_run.side_effect = clone

        skills = resolve_skills({
            "skills": [{
                "action": "install_from_repo",
                "url": "https://github.com/example/repo.git#main",
            }]
        })

        self.assertEqual(skills, [Skill("alpha", "cloned")])
        self.assertIn("--branch", mock_run.call_args.args[0])

    @patch("generators.models.skills_catalog.subprocess.run")
    def test_a_failed_clone_is_fatal(self, mock_run):
        mock_run.side_effect = subprocess.CalledProcessError(1, "git")

        with self.assertRaises(SkillCatalogError):
            resolve_skills({"skills": ["https://github.com/example/repo.git"]})


class ProseSkillCoverageTest(unittest.TestCase):
    """Coverage for a skill catalog shipping no scripts, scored on expected_skills.

    Most skills in the wild are prose-only, so this is the common shape rather
    than an edge case.
    """

    _CATALOG = [
        Skill("alpha", "runs alpha things"),
        Skill("beta", "runs beta things"),
        Skill("gamma"),
        Skill("delta"),
    ]

    def _run(self, scenarios, skills=None):
        return TrajectoryCoverageScorer({}, {}).run(
            _context(scenarios, [], self._CATALOG if skills is None else skills)
        )

    def test_skills_outside_the_catalog_do_not_count_as_coverage(self):
        contribution = self._run(
            [{"id": "c1", "expected_skills": ["alpha", "retired-skill"]}]
        )

        self.assertEqual(contribution.score, 25)
        self.assertEqual(contribution.metrics["capabilities_covered"], 1)
        self.assertEqual(contribution.metrics["capabilities_total"], 4)

    def test_a_gap_carries_the_catalog_description(self):
        # Synthesis may only reason from the report, and for a prose-only skill
        # the description is the sole record of what it does.
        contribution = self._run([{"id": "c1", "expected_skills": ["alpha"]}])

        self.assertIn("beta (runs beta things)", contribution.suggestions[0])
        self.assertIn("gamma", contribution.suggestions[0])

    def test_a_string_expected_skills_is_ignored_rather_than_split(self):
        # Iterating a str registers each letter as a covered skill name.
        contribution = self._run(
            [{"id": "c1", "expected_skills": "alpha"}], [Skill("a")]
        )

        self.assertEqual(contribution.score, 0)

    def test_a_trajectory_does_not_cover_a_prose_skill(self):
        # expected_trajectory names operations, which a prose skill has none of.
        contribution = self._run([{"id": "c1", "expected_trajectory": ["alpha"]}])

        self.assertEqual(contribution.score, 0)


class CompositionScorerTest(unittest.TestCase):
    """Only the composable-surface gate; the judge itself is out of scope."""

    # A judge response that grades cleanly, so an inapplicable result can only
    # have come from the surface gate and never from a failed judge call.
    _RESPONSE = json.dumps({
        KEY_MULTI_TOOL: ["c1"],
        KEY_SEQUENCE_DEPENDENCY: ["c1"],
    })

    def _scorer(self):
        with patch("scorers.dataset_quality.context.get_generator") as generator:
            generator.return_value = _StubModel(self._RESPONSE)
            return CompositionScorer({"model_config": "model.yaml"}, {})

    def test_a_string_trajectory_does_not_fabricate_a_composable_surface(self):
        # Iterating a str makes one CUJ look like a surface of 9 distinct units,
        # so the gate opens and composition is graded on a product it cannot see.
        context = _context([{"id": "c1", "expected_trajectory": "run_script"}], [])

        contribution = self._scorer().run(context)

        self.assertFalse(contribution.applicable)

    def test_a_list_trajectory_still_opens_the_gate(self):
        context = _context(
            [{"id": "c1", "expected_trajectory": ["alpha", "beta"]}], []
        )

        contribution = self._scorer().run(context)

        self.assertTrue(contribution.applicable)
        self.assertEqual(contribution.score, 100)


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
        self.assertEqual(contribution.metrics["tool_named_cuj_count"], 1)

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

        self.assertEqual(at_target.metrics["tool_named_cuj_count"], 1)
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


def _setup(**overrides):
    setup = {"mcp_servers": {"widget": {"httpUrl": "https://x"}}}
    setup.update(overrides)
    return setup


def _model_config(**overrides):
    return {"setup": _setup(**overrides)}


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


class _CollidingDistributionScorer(SubScorer):
    """Emits a distribution keyed like a reserved report field."""

    name = "colliding_distribution"
    category = CATEGORY_DISCOVERABILITY
    default_weight = 10

    def run(self, context):
        return SubScoreContribution(
            score=100, distribution={"letter_grade": "clobbered"}
        )


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

    def test_registry_default_weights_are_a_fixed_budget(self):
        # graded_weight/total_weight are only readable as a share of the rubric
        # because the defaults sum to a fixed budget.
        total = sum(cls.default_weight for cls in SCORER_REGISTRY.values())
        activation = sum(
            cls.default_weight for cls in SCORER_REGISTRY.values()
            if cls.category == CATEGORY_TOOL_ACTIVATION
        )

        self.assertEqual(total, 100)
        self.assertEqual(activation, _TRAJECTORY_WEIGHT)


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

    def test_unexpected_payload_shapes_return_empty_rather_than_raise(self):
        # A raise here escapes compare() and the caller turns it into a score of
        # 0, which is indistinguishable from a genuine F.
        self.assertEqual(self.scorer._extract_cujs("[1, 2]"), [])
        self.assertEqual(self.scorer._extract_cujs('{"scenario": "oops"}'), [])
        self.assertEqual(self.scorer._extract_cujs({"scenario": []}), [])
        self.assertEqual(
            self.scorer._extract_cujs({"scenario": {"all_cujs": "c1"}}), []
        )

    def test_non_dict_cujs_are_dropped(self):
        cujs = self.scorer._extract_cujs(
            {"scenario": {"all_cujs": [{"id": "c1"}, "junk", None]}}
        )

        self.assertEqual(cujs, [{"id": "c1"}])


class FetchToolsTest(unittest.TestCase):

    def setUp(self):
        self.scorer = DatasetQualityScorer(_config(), {})

    def test_a_setup_without_mcp_servers_has_nothing_to_query(self):
        # Not an error: a skills-only product still has a surface to grade.
        self.assertEqual(self.scorer._fetch_tools({}), [])

    @patch("scorers.dataset_quality.scorer.time.sleep")
    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    def test_a_transient_failure_is_retried(self, mock_fetch, _sleep):
        mock_fetch.side_effect = [McpToolsError("blip"), [_tool("alpha")]]

        self.assertEqual(self.scorer._fetch_tools(_setup()), [_tool("alpha")])
        self.assertEqual(mock_fetch.call_count, 2)

    @patch("scorers.dataset_quality.scorer.time.sleep")
    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    def test_repeated_failures_surface_after_the_last_attempt(
        self, mock_fetch, _sleep
    ):
        mock_fetch.side_effect = McpToolsError("down")

        with self.assertRaises(McpToolsError):
            self.scorer._fetch_tools(_setup())
        self.assertEqual(mock_fetch.call_count, 3)

    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    def test_an_empty_catalog_is_not_retried(self, mock_fetch):
        mock_fetch.return_value = []

        with self.assertRaises(McpToolsError):
            self.scorer._fetch_tools(_setup())
        self.assertEqual(mock_fetch.call_count, 1)


class ActivationCatalogTest(unittest.TestCase):
    """End-to-end: a declared skills catalog reaches the scorer and is graded.

    The per-channel scoring itself is covered by the unit tests above; what runs
    only here is the config -> resolve_skills -> context.skills wiring.
    """

    def setUp(self):
        self.scorer = DatasetQualityScorer(
            _config(sub_scorers={"trajectory_coverage": {}}), {}
        )

    @patch(
        "scorers.dataset_quality.scorer.resolve_skills",
        return_value=[Skill("s1"), Skill("s2")],
    )
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_a_prose_only_skill_catalog_is_graded_on_the_skills(
        self, mock_load, _skills
    ):
        # Most skills ship no scripts, so a product declaring only skills would
        # otherwise go entirely ungraded on activation.
        mock_load.return_value = {"setup": {"skills": ["repo"]}}

        rows = _compare(
            self.scorer, _wrapper([{"id": "c1", "expected_skills": ["s1"]}])
        )

        summary = json.loads(rows[0][2])
        self.assertEqual(summary["excluded_scorers"], [])
        self.assertEqual(summary["dataset_quality_score"], 50)

    @patch("scorers.dataset_quality.scorer.resolve_skills", return_value=[])
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_a_product_declaring_neither_channel_scores_null(
        self, mock_load, _skills
    ):
        mock_load.return_value = {"setup": {}}

        rows = _compare(self.scorer, _wrapper([{"id": "c1"}]))

        self.assertEqual(len(rows), 1)
        self.assertIsNone(rows[0][1])
        self.assertIn(
            "no tools or skills configured", json.loads(rows[0][2])["error"]
        )


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
        self.assertIn("capability discovery failed", json.loads(reason)["error"])

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

    @patch("scorers.dataset_quality.scorer.AgentCliGenerator.fetch_mcp_tools")
    @patch("scorers.dataset_quality.scorer.load_yaml_config")
    def test_a_distribution_cannot_overwrite_a_report_field(
        self, mock_load, mock_fetch
    ):
        mock_load.return_value = _model_config()
        mock_fetch.return_value = [_tool("alpha")]
        registry = dict(
            SCORER_REGISTRY, colliding_distribution=_CollidingDistributionScorer
        )
        with patch("scorers.dataset_quality.scorer.SCORER_REGISTRY", registry):
            scorer = DatasetQualityScorer(
                _config(sub_scorers={"colliding_distribution": {}}), {}
            )
            rows = _compare(scorer, _wrapper([{"id": "c1"}]))

        summary = json.loads(rows[0][2])
        self.assertEqual(summary["letter_grade"], "A")

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
