"""Tests for baseline carry-forward in McpStyleReadabilityScorer."""

import json
import unittest

from mcp import types as mcp_types

from evaluator.mcp_readability.baseline import Baseline
from scorers.mcp_readability import carry_forward as cf
from scorers.mcp_readability.fingerprint import tool_fingerprints
from scorers.mcp_readability.scoring import EndpointContext
from scorers.mcp_readability.style_readability import McpStyleReadabilityScorer


def _tool(name, description="Does a thing."):
    return mcp_types.Tool(
        name=name, description=description, inputSchema={"type": "object"}
    )


def _entry(tool, *findings):
    return {"tool": tool, "findings": list(findings)}


def _finding(severity="P1", rule_id="Tool Names", title="", locator=""):
    finding = {"severity": severity, "rule_id": rule_id}
    if title:
        finding["title"] = title
    if locator:
        finding["locator"] = locator
    return finding


class _RecordingModel:
    """A judge that counts calls and returns canned responses in order."""

    def __init__(self, *payloads):
        self.payloads = list(payloads)
        self.calls = 0
        self.prompts = []

    def generate(self, prompt):
        self.calls += 1
        self.prompts.append(prompt)
        return json.dumps(self.payloads[min(self.calls, len(self.payloads)) - 1])


def _focus_lines(model):
    """The prompt's scope line, naming the tools the judge must report on."""
    return [
        line
        for line in model.prompts[0].splitlines()
        if "have changed" in line
    ]


def _scorer(model):
    scorer = McpStyleReadabilityScorer.__new__(McpStyleReadabilityScorer)
    scorer.name = "mcp_style_readability"
    scorer.style_guide = "guide"
    scorer.style_guide_sha = "guide-sha"
    scorer.judge_model = "fake-model"
    scorer.model = model
    return scorer


def _run(scorer, tools, baseline=None, override_reason="", exceptions=None):
    fingerprints = tool_fingerprints(tools)
    context = EndpointContext(
        product_name="AlloyDB",
        endpoint={},
        tools=tools,
        man_page="man page",
        exceptions=exceptions or [],
        baseline=cf.BaselineContext(
            endpoint_key="key",
            tool_fingerprints=fingerprints,
            baseline=baseline,
            override_reason=override_reason,
        ),
    )
    return scorer.run(context)


def _baseline_from(contribution, tools, **overrides):
    """The Baseline a next run would load from this run's result row."""
    fields = contribution.row_fields
    baseline = Baseline(
        endpoint_key="key",
        job_id="job-1",
        check_timestamp="2026-09-09T00:00:00Z",
        judge_fingerprint=fields["mcp_readability_judge_fingerprint"],
        judge_components=json.loads(
            fields["mcp_readability_judge_components_json"]
        ),
        tool_fingerprints=tool_fingerprints(tools),
        feedback=json.loads(fields["mcp_readability_llm_feedback_json"]),
        readability_score=fields["mcp_readability_score"],
    )
    for key, value in overrides.items():
        setattr(baseline, key, value)
    return baseline


class UnchangedEndpointTest(unittest.TestCase):
    """The invariant: same inputs => same bytes, no model call."""

    def setUp(self):
        self.tools = [_tool("list_instances"), _tool("create_instance")]
        self.payload = {
            "readability_score": 72,
            "findings_by_tool": [
                _entry("create_instance", _finding("P0", "Avoid complex params")),
                _entry("list_instances", _finding("P2", "Concise Descriptions")),
            ],
            "summary": "Mostly fine.",
        }

    def test_no_model_call_and_identical_feedback(self):
        first_model = _RecordingModel(self.payload)
        first = _run(_scorer(first_model), self.tools)
        self.assertEqual(first_model.calls, 1)

        second_model = _RecordingModel(self.payload)
        second = _run(
            _scorer(second_model),
            self.tools,
            baseline=_baseline_from(first, self.tools),
        )

        self.assertEqual(second_model.calls, 0)
        self.assertEqual(
            second.row_fields["mcp_readability_feedback_mode"], cf.MODE_CARRIED
        )
        self.assertEqual(
            second.row_fields["mcp_readability_change_reason"], cf.UNCHANGED
        )
        # Byte-identical findings, counts and score.
        self.assertEqual(
            _findings_json(second), _findings_json(first)
        )
        for column in (
            "mcp_readability_p0_issues",
            "mcp_readability_p1_issues",
            "mcp_readability_p2_issues",
            "mcp_readability_score",
        ):
            self.assertEqual(
                second.row_fields[column], first.row_fields[column], column
            )

    def test_reordering_tools_still_carries(self):
        first = _run(_scorer(_RecordingModel(self.payload)), self.tools)
        model = _RecordingModel(self.payload)
        reordered = list(reversed(self.tools))
        second = _run(
            _scorer(model), reordered, baseline=_baseline_from(first, self.tools)
        )
        self.assertEqual(model.calls, 0)
        self.assertEqual(
            second.row_fields["mcp_readability_change_reason"], cf.UNCHANGED
        )


class PartialRunTest(unittest.TestCase):
    """Only changed tools are re-judged; the rest keep their findings."""

    def setUp(self):
        self.tools = [_tool("list_instances"), _tool("create_instance")]
        self.baseline_payload = {
            "readability_score": 60,
            "findings_by_tool": [
                _entry(
                    "create_instance",
                    _finding("P0", "Avoid complex params", title="nested config"),
                    _finding("P1", "Tool Names", title="verb missing"),
                ),
                _entry("list_instances", _finding("P2", "Concise Descriptions")),
            ],
            "summary": "Baseline summary.",
        }
        self.first = _run(
            _scorer(_RecordingModel(self.baseline_payload)), self.tools
        )

    def _changed_tools(self):
        return [_tool("list_instances"), _tool("create_instance", "Rewritten.")]

    def test_unchanged_tool_findings_are_not_taken_from_the_judge(self):
        # The judge volunteers findings for the unchanged tool too; they must be
        # discarded, because the filter -- not the prompt -- is the guarantee.
        model = _RecordingModel(
            {
                "readability_score": 90,
                "findings_by_tool": [
                    _entry("create_instance", _finding("P2", "Tool Names")),
                    _entry(
                        "list_instances",
                        _finding("P0", "Hallucinated"),
                    ),
                ],
                "summary": "New summary.",
            }
        )
        contribution = _run(
            _scorer(model),
            self._changed_tools(),
            baseline=_baseline_from(self.first, self.tools),
        )
        self.assertEqual(model.calls, 1)
        self.assertEqual(
            contribution.row_fields["mcp_readability_feedback_mode"],
            cf.MODE_PARTIAL,
        )
        by_tool = _by_tool(contribution)
        self.assertEqual(
            [finding["rule_id"] for finding in by_tool["list_instances"]],
            ["Concise Descriptions"],  # carried, not the hallucinated P0
        )
        self.assertEqual(
            [finding["rule_id"] for finding in by_tool["create_instance"]],
            ["Tool Names"],
        )
        self.assertEqual(contribution.row_fields["mcp_readability_p0_issues"], 0)

    def test_prompt_names_the_tools_to_report_on(self):
        model = _RecordingModel({"readability_score": 90, "findings_by_tool": []})
        _run(
            _scorer(model),
            self._changed_tools(),
            baseline=_baseline_from(self.first, self.tools),
        )
        prompt = model.prompts[0]
        self.assertIn("SCOPE OF THIS REVIEW", prompt)
        self.assertIn("create_instance", prompt)
        # The whole man page is still supplied, for severity calibration.
        self.assertIn("man page", prompt)

    def test_removed_tool_findings_are_dropped_and_counts_recomputed(self):
        model = _RecordingModel(
            {"readability_score": 90, "findings_by_tool": [], "summary": "s"}
        )
        contribution = _run(
            _scorer(model),
            [_tool("list_instances")],  # create_instance removed
            baseline=_baseline_from(self.first, self.tools),
        )
        by_tool = _by_tool(contribution)
        self.assertNotIn("create_instance", by_tool)
        self.assertEqual(contribution.row_fields["mcp_readability_p0_issues"], 0)
        self.assertEqual(contribution.row_fields["mcp_readability_p1_issues"], 0)
        self.assertEqual(contribution.row_fields["mcp_readability_p2_issues"], 1)
        provenance = _provenance(contribution)
        self.assertEqual(provenance["removed_tools"], ["create_instance"])

    def test_added_tool_is_marked_new(self):
        model = _RecordingModel(
            {
                "readability_score": 80,
                "findings_by_tool": [_entry("delete_instance", _finding("P1"))],
                "summary": "s",
            }
        )
        contribution = _run(
            _scorer(model),
            self.tools + [_tool("delete_instance")],
            baseline=_baseline_from(self.first, self.tools),
        )
        provenance = _provenance(contribution)
        self.assertEqual(provenance["added_tools"], ["delete_instance"])
        self.assertEqual(provenance["tool_provenance"]["delete_instance"], "new")
        self.assertEqual(
            provenance["tool_provenance"]["list_instances"], "carried"
        )

    def test_carried_findings_keep_their_finding_id(self):
        before = _by_tool(self.first)["list_instances"][0]["finding_id"]
        contribution = _run(
            _scorer(
                _RecordingModel({"readability_score": 90, "findings_by_tool": []})
            ),
            self._changed_tools(),
            baseline=_baseline_from(self.first, self.tools),
        )
        after = _by_tool(contribution)["list_instances"][0]["finding_id"]
        self.assertEqual(before, after)

    def test_general_entry_is_carried_when_only_descriptions_changed(self):
        first = _run(
            _scorer(
                _RecordingModel(
                    {
                        "readability_score": 60,
                        "findings_by_tool": [
                            _entry("general", _finding("P1", "Tool Count Limits")),
                            _entry("list_instances", _finding("P2")),
                        ],
                        "summary": "s",
                    }
                )
            ),
            self.tools,
        )
        contribution = _run(
            _scorer(
                _RecordingModel(
                    {
                        "readability_score": 90,
                        "findings_by_tool": [
                            _entry("general", _finding("P0", "Invented")),
                        ],
                        "summary": "s",
                    }
                )
            ),
            self._changed_tools(),
            baseline=_baseline_from(first, self.tools),
        )
        self.assertEqual(
            [
                finding["rule_id"]
                for finding in _by_tool(contribution)["general"]
            ],
            ["Tool Count Limits"],
        )

    def test_general_entry_is_refreshed_when_the_tool_set_changes(self):
        first = _run(
            _scorer(
                _RecordingModel(
                    {
                        "readability_score": 60,
                        "findings_by_tool": [
                            _entry("general", _finding("P1", "Tool Count Limits"))
                        ],
                        "summary": "s",
                    }
                )
            ),
            self.tools,
        )
        contribution = _run(
            _scorer(
                _RecordingModel(
                    {
                        "readability_score": 90,
                        "findings_by_tool": [
                            _entry("general", _finding("P1", "Inconsistent Naming"))
                        ],
                        "summary": "s",
                    }
                )
            ),
            self.tools + [_tool("delete_instance")],
            baseline=_baseline_from(first, self.tools),
        )
        self.assertEqual(
            [
                finding["rule_id"]
                for finding in _by_tool(contribution)["general"]
            ],
            ["Inconsistent Naming"],
        )

    def test_general_entry_is_rendered_first(self):
        first = _run(
            _scorer(
                _RecordingModel(
                    {
                        "readability_score": 60,
                        "findings_by_tool": [
                            _entry("list_instances", _finding("P2")),
                            _entry("general", _finding("P1")),
                        ],
                        "summary": "s",
                    }
                )
            ),
            self.tools,
        )
        contribution = _run(
            _scorer(
                _RecordingModel({"readability_score": 90, "findings_by_tool": []})
            ),
            self._changed_tools(),
            baseline=_baseline_from(first, self.tools),
        )
        tools = [entry["tool"] for entry in _findings(contribution)]
        self.assertEqual(tools[0], "general")


class CrossToolFindingsTest(unittest.TestCase):
    """Cross-tool ``general`` findings are carried or re-judged on partial runs."""

    def setUp(self):
        self.tools = [_tool("a"), _tool("b")]
        self.first = _run(
            _scorer(
                _RecordingModel(
                    {
                        "readability_score": 60,
                        "findings_by_tool": [
                            _entry("general", _finding("P1", "Tool Count Limits")),
                            _entry("a", _finding("P2")),
                        ],
                        "summary": "s",
                    }
                )
            ),
            self.tools,
        )

    def _obedient_judge(self):
        """A judge that emits nothing it was not asked for."""
        return _RecordingModel(
            {"readability_score": 60, "findings_by_tool": [], "summary": "s"}
        )

    def test_adding_a_tool_asks_the_judge_for_general(self):
        model = self._obedient_judge()
        _run(
            _scorer(model),
            self.tools + [_tool("c")],
            baseline=_baseline_from(self.first, self.tools),
        )
        focus = _focus_lines(model)
        self.assertTrue(focus)
        self.assertIn("general", focus[0])

    def test_removing_a_tool_asks_the_judge_for_general(self):
        model = self._obedient_judge()
        _run(
            _scorer(model),
            [_tool("a")],
            baseline=_baseline_from(self.first, self.tools),
        )
        focus = _focus_lines(model)
        self.assertTrue(focus, "a removal-only run must still scope the prompt")
        self.assertIn("general", focus[0])

    def test_description_only_edit_does_not_ask_for_general(self):
        # No name change => the baseline's general entry is carried, so there is
        # nothing to ask for and no output tokens to spend.
        model = self._obedient_judge()
        _run(
            _scorer(model),
            [_tool("a"), _tool("b", "Rewritten.")],
            baseline=_baseline_from(self.first, self.tools),
        )
        focus = _focus_lines(model)
        self.assertNotIn("general", focus[0])

    def test_a_fresh_general_still_wins_when_the_judge_provides_one(self):
        model = _RecordingModel(
            {
                "readability_score": 60,
                "findings_by_tool": [
                    _entry("general", _finding("P1", "Inconsistent Naming"))
                ],
                "summary": "s",
            }
        )
        contribution = _run(
            _scorer(model),
            self.tools + [_tool("c")],
            baseline=_baseline_from(self.first, self.tools),
        )
        self.assertEqual(
            [
                finding["rule_id"]
                for finding in _by_tool(contribution)["general"]
            ],
            ["Inconsistent Naming"],
        )


class BaselineIsolationTest(unittest.TestCase):
    """Merging must not write into the shared Baseline the store handed out."""

    def test_carried_findings_do_not_mutate_the_baseline(self):
        tools = [_tool("a")]
        first = _run(
            _scorer(
                _RecordingModel(
                    {
                        "readability_score": 60,
                        "findings_by_tool": [_entry("a", _finding("P1"))],
                        "summary": "s",
                    }
                )
            ),
            tools,
        )
        baseline = _baseline_from(first, tools)
        # Strip the ids a previous generation minted, as an old baseline would.
        for entry in baseline.feedback["findings_by_tool"]:
            for finding in entry["findings"]:
                finding.pop("finding_id", None)
        snapshot = json.dumps(baseline.feedback, sort_keys=True)

        _run(_scorer(_RecordingModel({})), tools, baseline=baseline)

        self.assertEqual(
            json.dumps(baseline.feedback, sort_keys=True),
            snapshot,
            "the store's Baseline was mutated in place",
        )

    def test_two_endpoints_sharing_a_baseline_do_not_interfere(self):
        tools = [_tool("a")]
        first = _run(
            _scorer(
                _RecordingModel(
                    {
                        "readability_score": 60,
                        "findings_by_tool": [_entry("a", _finding("P1"))],
                        "summary": "s",
                    }
                )
            ),
            tools,
        )
        shared = _baseline_from(first, tools)
        one = _run(_scorer(_RecordingModel({})), tools, baseline=shared)
        two = _run(_scorer(_RecordingModel({})), tools, baseline=shared)
        self.assertEqual(_findings_json(one), _findings_json(two))


class ChangeReasonTest(unittest.TestCase):
    """Each judge input that changes names itself in the reason column."""

    def setUp(self):
        self.tools = [_tool("list_instances")]
        self.payload = {
            "readability_score": 70,
            "findings_by_tool": [_entry("list_instances", _finding("P1"))],
            "summary": "s",
        }
        self.first = _run(_scorer(_RecordingModel(self.payload)), self.tools)

    def _reason_after(self, **scorer_changes):
        scorer = _scorer(_RecordingModel(self.payload))
        for key, value in scorer_changes.items():
            setattr(scorer, key, value)
        contribution = _run(
            scorer, self.tools, baseline=_baseline_from(self.first, self.tools)
        )
        return contribution.row_fields["mcp_readability_change_reason"]

    def test_style_guide_change(self):
        self.assertEqual(
            self._reason_after(style_guide_sha="other"), cf.STYLE_GUIDE_CHANGED
        )

    def test_model_change(self):
        self.assertEqual(
            self._reason_after(judge_model="gemini-2.5-pro"), cf.MODEL_CHANGED
        )

    def test_product_rename_is_an_identity_change(self):
        scorer = _scorer(_RecordingModel(self.payload))
        context = EndpointContext(
            product_name="AlloyDB Omni",  # renamed, explicit id kept the baseline
            endpoint={},
            tools=self.tools,
            man_page="man page",
            exceptions=[],
            baseline=cf.BaselineContext(
                endpoint_key="key",
                tool_fingerprints=tool_fingerprints(self.tools),
                baseline=_baseline_from(self.first, self.tools),
            ),
        )
        self.assertEqual(
            scorer.run(context).row_fields["mcp_readability_change_reason"],
            cf.ENDPOINT_IDENTITY_CHANGED,
        )

    def test_waiver_change(self):
        contribution = _run(
            _scorer(_RecordingModel(self.payload)),
            self.tools,
            baseline=_baseline_from(self.first, self.tools),
            exceptions=[{"rule_id": "Tool Names", "reason": "legacy"}],
        )
        self.assertEqual(
            contribution.row_fields["mcp_readability_change_reason"],
            cf.WAIVERS_CHANGED,
        )

    def test_expiry_still_reports_the_delta_against_the_old_review(self):
        # An expired baseline must not supply findings, but it is still the
        # reference for what moved -- otherwise every finding reads as new.
        contribution = _run(
            _scorer(_RecordingModel(self.payload)),
            self.tools,
            baseline=_baseline_from(self.first, self.tools),
            override_reason=cf.BASELINE_EXPIRED,
        )
        provenance = _provenance(contribution)
        self.assertEqual(provenance["previous_finding_count"], 1)
        self.assertEqual(provenance["new_finding_ids"], [])
        self.assertEqual(provenance["resolved_finding_ids"], [])
        self.assertEqual(
            provenance["baseline_timestamp"], "2026-09-09T00:00:00Z"
        )

    def test_a_baseline_without_components_is_not_called_a_first_run(self):
        baseline = _baseline_from(self.first, self.tools)
        baseline.judge_components = {}  # written before components were recorded
        baseline.judge_fingerprint = "stale"
        contribution = _run(
            _scorer(_RecordingModel(self.payload)), self.tools, baseline=baseline
        )
        self.assertEqual(
            contribution.row_fields["mcp_readability_change_reason"],
            cf.BASELINE_UNAVAILABLE,
        )

    def test_override_reasons_force_a_full_judge(self):
        for reason in (
            cf.FORCED_REFRESH,
            cf.BASELINE_EXPIRED,
            cf.BASELINE_UNAVAILABLE,
        ):
            with self.subTest(reason=reason):
                model = _RecordingModel(self.payload)
                contribution = _run(
                    _scorer(model),
                    self.tools,
                    baseline=_baseline_from(self.first, self.tools),
                    override_reason=reason,
                )
                self.assertEqual(model.calls, 1)
                self.assertEqual(
                    contribution.row_fields["mcp_readability_change_reason"],
                    reason,
                )


def _findings(contribution):
    return json.loads(
        contribution.row_fields["mcp_readability_llm_feedback_json"]
    )["findings_by_tool"]


def _findings_json(contribution):
    return json.dumps(_findings(contribution), sort_keys=True)


def _by_tool(contribution):
    return {
        entry["tool"]: entry["findings"]
        for entry in _findings(contribution)
    }


def _provenance(contribution):
    return json.loads(
        contribution.row_fields["mcp_readability_feedback_provenance_json"]
    )


if __name__ == "__main__":
    unittest.main()
