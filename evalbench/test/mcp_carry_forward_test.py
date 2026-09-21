"""Unit tests for the readability carry-forward decision and merge.

Two failures matter here and they fail in opposite directions. Carrying a
finding that should have been re-judged reports a stale surface as current;
re-judging a tool nobody touched puts the counts back at the mercy of the
model's non-determinism, which is the whole reason any of this exists. The
tests below pin each decision to the input that is supposed to drive it.
"""

import unittest

from evaluator.mcp_readability.baseline import Baseline
from scorers.mcp_readability import carry_forward as cf


_JUDGE = "judge-fp"

# Distinguishes "no baseline" from "default baseline" in _context below.
_DEFAULT = object()


def _baseline(tool_fingerprints=None, findings=None, **overrides):
    values = {
        "endpoint_key": "AlloyDB|http://x|PROD",
        "job_id": "job-old",
        "check_timestamp": "2026-09-01T00:00:00+00:00",
        "judge_fingerprint": _JUDGE,
        "judge_components": {"judge_model": "m", "style_guide_sha": "s"},
        "tool_fingerprints": tool_fingerprints or {"a": "fp-a", "b": "fp-b"},
        "feedback": {
            "findings_by_tool": findings
            if findings is not None
            else [
                {"tool": "a", "findings": [_finding("R1", "A finding")]},
                {"tool": "b", "findings": [_finding("R2", "B finding")]},
            ],
            "waived": [{"rule_id": "w", "reason": "why"}],
            "summary": "previous summary",
        },
        "readability_score": 70,
    }
    values.update(overrides)
    # Ids are minted when a finding is generated, so a real baseline always
    # arrives carrying them; without that, every carried finding would look new.
    cf.mint_finding_ids(values["feedback"].get("findings_by_tool") or [])
    return Baseline(**values)


def _finding(rule_id, title, **extra):
    finding = {
        "severity": "P1",
        "rule_id": rule_id,
        "title": title,
        "message": "m",
        "suggestion": "s",
    }
    finding.update(extra)
    return finding


def _context(current=None, baseline=_DEFAULT, **overrides):
    return cf.BaselineContext(
        endpoint_key="AlloyDB|http://x|PROD",
        job_id="job-new",
        tool_fingerprints=(
            current if current is not None else {"a": "fp-a", "b": "fp-b"}
        ),
        baseline=_baseline() if baseline is _DEFAULT else baseline,
        **overrides,
    )


class DecideTest(unittest.TestCase):

    def test_no_context_is_a_full_judge(self):
        decision = cf.decide(None, _JUDGE)
        self.assertEqual(decision.mode, cf.MODE_FULL_JUDGE)
        self.assertEqual(decision.change_reason, cf.NO_BASELINE)
        self.assertTrue(decision.needs_model_call)

    def test_no_baseline_judges_every_current_tool(self):
        decision = cf.decide(_context(baseline=None), _JUDGE)
        self.assertEqual(decision.mode, cf.MODE_FULL_JUDGE)
        self.assertEqual(decision.change_reason, cf.NO_BASELINE)
        self.assertEqual(sorted(decision.rejudged_tools), ["a", "b"])

    def test_identical_surface_makes_no_model_call(self):
        decision = cf.decide(_context(), _JUDGE)
        self.assertEqual(decision.mode, cf.MODE_CARRIED)
        self.assertEqual(decision.change_reason, cf.UNCHANGED)
        self.assertEqual(sorted(decision.carried_tools), ["a", "b"])
        self.assertFalse(decision.needs_model_call)

    def test_only_the_changed_tool_is_rejudged(self):
        decision = cf.decide(
            _context({"a": "fp-a", "b": "EDITED"}), _JUDGE
        )
        self.assertEqual(decision.mode, cf.MODE_PARTIAL)
        self.assertEqual(decision.change_reason, cf.TOOLS_CHANGED)
        self.assertEqual(decision.rejudged_tools, ["b"])
        self.assertEqual(decision.carried_tools, ["a"])

    def test_added_and_removed_tools_are_reported_separately(self):
        decision = cf.decide(_context({"a": "fp-a", "c": "fp-c"}), _JUDGE)
        self.assertEqual(decision.added_tools, ["c"])
        self.assertEqual(decision.removed_tools, ["b"])
        self.assertTrue(decision.names_changed)
        # A removal alone still leaves the surviving tools carried.
        self.assertEqual(decision.carried_tools, ["a"])

    def test_a_changed_judge_invalidates_every_tool(self):
        """A new model or guide can judge an untouched tool differently."""
        decision = cf.decide(_context(), "different-judge-fp")
        self.assertEqual(decision.mode, cf.MODE_FULL_JUDGE)
        self.assertEqual(sorted(decision.rejudged_tools), ["a", "b"])
        self.assertEqual(decision.carried_tools, [])

    def test_override_reason_forces_a_full_judge_but_keeps_the_baseline(self):
        """Expiry and forced refresh still need "what changed since" to report."""
        decision = cf.decide(
            _context(override_reason=cf.BASELINE_EXPIRED), _JUDGE
        )
        self.assertEqual(decision.mode, cf.MODE_FULL_JUDGE)
        self.assertEqual(decision.change_reason, cf.BASELINE_EXPIRED)
        self.assertIsNotNone(decision.baseline)

    def test_reported_tools_include_general_only_when_names_changed(self):
        renamed = cf.decide(_context({"a": "fp-a", "c": "fp-c"}), _JUDGE)
        self.assertIn(cf.GENERAL, renamed.tools_to_report)
        edited = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        self.assertNotIn(cf.GENERAL, edited.tools_to_report)
        # general is never trusted as a per-tool entry.
        self.assertNotIn(cf.GENERAL, renamed.accepted_tools)


class DecideWithComponentsTest(unittest.TestCase):

    def _components(self, **overrides):
        components = {"judge_model": "m", "style_guide_sha": "s"}
        components.update(overrides)
        return components

    def test_unchanged_components_leave_the_carried_decision_alone(self):
        decision = cf.decide_with_components(
            _context(), _JUDGE, self._components()
        )
        self.assertEqual(decision.mode, cf.MODE_CARRIED)
        self.assertEqual(decision.change_reason, cf.UNCHANGED)

    def test_the_changed_component_names_the_reason(self):
        for override, reason in [
            ({"judge_model": "other"}, cf.MODEL_CHANGED),
            ({"style_guide_sha": "other"}, cf.STYLE_GUIDE_CHANGED),
            ({"exceptions": [{"rule_id": "r"}]}, cf.WAIVERS_CHANGED),
            ({"prompt_version": "2"}, cf.PROMPT_CHANGED),
        ]:
            with self.subTest(component=sorted(override)[0]):
                decision = cf.decide_with_components(
                    _context(), "new-fp", self._components(**override)
                )
                self.assertEqual(decision.change_reason, reason)

    def test_the_most_consequential_component_wins(self):
        """Several inputs can move at once; one reason is reported."""
        decision = cf.decide_with_components(
            _context(),
            "new-fp",
            self._components(judge_model="other", style_guide_sha="other"),
        )
        self.assertEqual(decision.change_reason, cf.MODEL_CHANGED)

    def test_a_baseline_without_components_says_so(self):
        """Diffing against {} would blame whichever key sorts first."""
        decision = cf.decide_with_components(
            _context(baseline=_baseline(judge_components={})),
            "new-fp",
            self._components(),
        )
        self.assertEqual(decision.change_reason, cf.BASELINE_UNAVAILABLE)
        self.assertEqual(decision.component_diff, [])

    def test_an_unmapped_component_falls_back_to_prompt_changed(self):
        self.assertEqual(
            cf._reason_for_components(["something_new"]), cf.PROMPT_CHANGED
        )

    def test_a_fingerprint_mismatch_with_no_component_diff_is_unavailable(self):
        self.assertEqual(
            cf._reason_for_components([]), cf.BASELINE_UNAVAILABLE
        )


class MergeFeedbackTest(unittest.TestCase):

    def _judged(self, entries, summary="fresh summary"):
        # The scorer mints ids as it parses the judge's response, so the merge
        # never sees an unidentified fresh finding. Matching a survivor against
        # the baseline depends on that, so the fixture has to do it too.
        cf.mint_finding_ids(entries)
        return {
            "findings_by_tool": entries,
            "waived": [{"rule_id": "w2", "reason": "fresh"}],
            "summary": summary,
        }

    def test_carried_mode_returns_the_baseline_verbatim(self):
        decision = cf.decide(_context(), _JUDGE)
        merged = cf.merge_feedback(decision, None, ["a", "b"])
        self.assertEqual(
            [entry["tool"] for entry in merged["findings_by_tool"]], ["a", "b"]
        )
        self.assertEqual(merged["summary"], "previous summary")

    def test_a_partial_run_keeps_fresh_findings_only_for_changed_tools(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        judged = self._judged(
            [
                {"tool": "a", "findings": [_finding("R9", "should be ignored")]},
                {"tool": "b", "findings": [_finding("R3", "fresh b")]},
            ]
        )
        merged = cf.merge_feedback(decision, judged, ["a", "b"])
        by_tool = {
            entry["tool"]: entry["findings"]
            for entry in merged["findings_by_tool"]
        }
        self.assertEqual(by_tool["a"][0]["title"], "A finding")
        self.assertEqual(by_tool["b"][0]["title"], "fresh b")

    def test_a_surviving_finding_keeps_its_previous_wording(self):
        """A re-judged tool is described afresh; the report must not move for it."""
        baseline = _baseline(
            findings=[
                {
                    "tool": "b",
                    "findings": [
                        _finding("R2", "B finding", locator="timeout_ms")
                    ],
                }
            ]
        )
        decision = cf.decide(
            _context({"a": "fp-a", "b": "EDITED"}, baseline=baseline), _JUDGE
        )
        merged = cf.merge_feedback(
            decision,
            self._judged(
                [{
                    "tool": "b",
                    "findings": [
                        _finding(
                            "R2",
                            "the judge reworded this",
                            locator="timeout_ms",
                            message="and this",
                        )
                    ],
                }]
            ),
            ["a", "b"],
        )
        finding = merged["findings_by_tool"][0]["findings"][0]
        self.assertEqual(finding["title"], "B finding")
        self.assertEqual(finding["message"], "m")

    def test_a_genuinely_new_finding_is_taken_from_the_judge(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        merged = cf.merge_feedback(
            decision,
            self._judged(
                [{"tool": "b", "findings": [_finding("R7", "brand new")]}]
            ),
            ["a", "b"],
        )
        by_tool = {
            entry["tool"]: entry["findings"]
            for entry in merged["findings_by_tool"]
        }
        self.assertEqual(by_tool["b"][0]["title"], "brand new")

    def test_the_filter_not_the_prompt_is_what_guarantees_carrying(self):
        """The judge is shown every tool, so it can volunteer extra entries."""
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        judged = self._judged(
            [{"tool": "a", "findings": [_finding("R9", "unsolicited")]}]
        )
        merged = cf.merge_feedback(decision, judged, ["a", "b"])
        titles = [
            finding["title"]
            for entry in merged["findings_by_tool"]
            for finding in entry["findings"]
        ]
        self.assertNotIn("unsolicited", titles)

    def test_a_removed_tool_drops_out_of_the_merged_findings(self):
        decision = cf.decide(_context({"a": "fp-a"}), _JUDGE)
        merged = cf.merge_feedback(decision, self._judged([]), ["a"])
        self.assertEqual(
            [entry["tool"] for entry in merged["findings_by_tool"]], ["a"]
        )

    def test_general_is_carried_unless_the_tool_names_changed(self):
        baseline = _baseline(
            findings=[
                {"tool": "a", "findings": [_finding("R1", "A finding")]},
                {
                    "tool": cf.GENERAL,
                    "findings": [_finding("R0", "old cross-tool")],
                },
            ]
        )
        edited = cf.decide(
            _context({"a": "EDITED", "b": "fp-b"}, baseline=baseline), _JUDGE
        )
        merged = cf.merge_feedback(edited, self._judged([]), ["a", "b"])
        self.assertEqual(
            merged["findings_by_tool"][0]["findings"][0]["title"],
            "old cross-tool",
        )

        renamed = cf.decide(
            _context({"a": "fp-a", "c": "fp-c"}, baseline=baseline), _JUDGE
        )
        fresh = cf.merge_feedback(
            renamed,
            self._judged(
                [{
                    "tool": cf.GENERAL,
                    "findings": [_finding("R0", "new cross-tool")],
                }]
            ),
            ["a", "c"],
        )
        self.assertEqual(
            fresh["findings_by_tool"][0]["findings"][0]["title"],
            "new cross-tool",
        )

    def test_entries_follow_man_page_order_with_general_first(self):
        baseline = _baseline(
            tool_fingerprints={"a": "fp-a", "b": "fp-b"},
            findings=[
                {"tool": "b", "findings": [_finding("R2", "B")]},
                {"tool": "a", "findings": [_finding("R1", "A")]},
                {"tool": cf.GENERAL, "findings": [_finding("R0", "G")]},
            ],
        )
        decision = cf.decide(_context(baseline=baseline), _JUDGE)
        merged = cf.merge_feedback(decision, None, ["a", "b"])
        self.assertEqual(
            [entry["tool"] for entry in merged["findings_by_tool"]],
            [cf.GENERAL, "a", "b"],
        )

    def test_merging_does_not_mutate_the_baseline(self):
        """One store serves every endpoint; a merge must not write into it."""
        baseline = _baseline()
        decision = cf.decide(_context(baseline=baseline), _JUDGE)
        merged = cf.merge_feedback(decision, None, ["a", "b"])
        merged["findings_by_tool"][0]["findings"][0]["title"] = "rewritten"
        original = baseline.feedback["findings_by_tool"][0]["findings"][0]
        self.assertEqual(original["title"], "A finding")

    def test_entries_without_findings_are_dropped(self):
        decision = cf.decide(_context(baseline=None), _JUDGE)
        merged = cf.merge_feedback(
            decision,
            self._judged([{"tool": "a", "findings": []}]),
            ["a"],
        )
        self.assertEqual(merged["findings_by_tool"], [])


class UnreportedFindingsTest(unittest.TestCase):
    """What the second pass is asked to rule on.

    Over-reporting here costs a model call on findings code could have decided;
    under-reporting silently resolves an issue that still exists, which is the
    failure the second pass exists to prevent.
    """

    def _judged(self, entries):
        cf.mint_finding_ids(entries)
        return {"findings_by_tool": entries}

    def test_a_dropped_finding_on_a_rejudged_tool_is_returned(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        unreported = cf.unreported_findings(decision, self._judged([]))
        self.assertEqual(
            [
                (entry["tool"], entry["findings"][0]["title"])
                for entry in unreported
            ],
            [("b", "B finding")],
        )

    def test_a_carried_tool_is_never_up_for_reconciliation(self):
        """Tool a was not re-judged, so its silence means nothing."""
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        unreported = cf.unreported_findings(decision, self._judged([]))
        self.assertNotIn("a", [entry["tool"] for entry in unreported])

    def test_nothing_is_returned_when_the_judge_repeated_everything(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        unreported = cf.unreported_findings(
            decision,
            self._judged(
                [{"tool": "b", "findings": [_finding("R2", "B finding")]}]
            ),
        )
        self.assertEqual(unreported, [])

    def test_an_added_tool_has_nothing_to_reconcile(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "fp-b", "c": "fp-c"}),
                             _JUDGE)
        self.assertEqual(cf.unreported_findings(decision, self._judged([])), [])

    def test_a_carried_run_never_reaches_the_second_pass(self):
        decision = cf.decide(_context(), _JUDGE)
        self.assertEqual(decision.mode, cf.MODE_CARRIED)
        self.assertEqual(cf.unreported_findings(decision, None), [])

    def test_general_is_in_scope_only_when_the_tool_names_changed(self):
        baseline = _baseline(
            findings=[
                {"tool": cf.GENERAL, "findings": [_finding("R0", "cross-tool")]}
            ]
        )
        edited = cf.decide(
            _context({"a": "EDITED", "b": "fp-b"}, baseline=baseline), _JUDGE
        )
        self.assertEqual(cf.unreported_findings(edited, self._judged([])), [])

        renamed = cf.decide(
            _context({"a": "fp-a", "c": "fp-c"}, baseline=baseline), _JUDGE
        )
        self.assertEqual(
            [
                entry["tool"]
                for entry in cf.unreported_findings(
                    renamed, self._judged([])
                )
            ],
            [cf.GENERAL],
        )


class RestoreTest(unittest.TestCase):
    """Findings the second pass ruled still apply, put back into the report."""

    def _judged(self, entries):
        cf.mint_finding_ids(entries)
        return {"findings_by_tool": entries, "waived": [], "summary": "s"}

    def test_a_restored_finding_returns_with_its_original_wording(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        judged = self._judged([])
        restored = cf.unreported_findings(decision, judged)
        merged = cf.merge_feedback(
            decision, judged, ["a", "b"], restored=restored
        )
        by_tool = {
            entry["tool"]: entry["findings"]
            for entry in merged["findings_by_tool"]
        }
        self.assertEqual(by_tool["b"][0]["title"], "B finding")

    def test_a_tool_that_lost_every_finding_gets_its_entry_back(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        merged = cf.merge_feedback(
            decision,
            self._judged([]),
            ["a", "b"],
            restored=[
                {"tool": "b", "findings": [_finding("R2", "B finding")]}
            ],
        )
        self.assertIn(
            "b", [entry["tool"] for entry in merged["findings_by_tool"]]
        )

    def test_a_restored_p0_leads_its_entry(self):
        baseline = _baseline(
            findings=[
                {
                    "tool": "b",
                    "findings": [_finding("R2", "blocker", severity="P0")],
                }
            ]
        )
        decision = cf.decide(
            _context({"a": "fp-a", "b": "EDITED"}, baseline=baseline), _JUDGE
        )
        judged = self._judged(
            [{"tool": "b", "findings": [_finding("R5", "minor")]}]
        )
        merged = cf.merge_feedback(
            decision,
            judged,
            ["a", "b"],
            restored=cf.unreported_findings(decision, judged),
        )
        by_tool = {
            entry["tool"]: entry["findings"]
            for entry in merged["findings_by_tool"]
        }
        self.assertEqual(
            [finding["title"] for finding in by_tool["b"]],
            ["blocker", "minor"],
        )

    def test_restoring_never_duplicates_a_finding_the_judge_repeated(self):
        # Restored findings come off the baseline, so they always carry ids.
        restored = [{"tool": "b", "findings": [_finding("R2", "B finding")]}]
        cf.mint_finding_ids(restored)
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        merged = cf.merge_feedback(
            decision,
            self._judged(
                [{"tool": "b", "findings": [_finding("R2", "B finding")]}]
            ),
            ["a", "b"],
            restored=restored,
        )
        by_tool = {
            entry["tool"]: entry["findings"]
            for entry in merged["findings_by_tool"]
        }
        self.assertEqual(len(by_tool["b"]), 1)

    def test_a_resolved_finding_stays_out(self):
        """The second pass ruling "fixed" must actually drop the finding."""
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        merged = cf.merge_feedback(
            decision, self._judged([]), ["a", "b"], restored=[]
        )
        self.assertEqual(
            [entry["tool"] for entry in merged["findings_by_tool"]], ["a"]
        )


class FindingIdTest(unittest.TestCase):

    def test_the_same_finding_gets_the_same_id(self):
        first = [{"tool": "a", "findings": [_finding("R1", "Title")]}]
        second = [{"tool": "a", "findings": [_finding("R1", "Title")]}]
        cf.mint_finding_ids(first)
        cf.mint_finding_ids(second)
        self.assertEqual(
            first[0]["findings"][0]["finding_id"],
            second[0]["findings"][0]["finding_id"],
        )

    def test_the_same_rule_twice_on_one_tool_stays_distinguishable(self):
        entries = [
            {
                "tool": "a",
                "findings": [
                    _finding("R1", "first", locator="param_x"),
                    _finding("R1", "second", locator="param_y"),
                ],
            }
        ]
        cf.mint_finding_ids(entries)
        ids = {finding["finding_id"] for finding in entries[0]["findings"]}
        self.assertEqual(len(ids), 2)

    def test_identical_findings_are_separated_by_an_ordinal(self):
        entries = [
            {
                "tool": "a",
                "findings": [_finding("R1", "same"), _finding("R1", "same")],
            }
        ]
        cf.mint_finding_ids(entries)
        ids = [finding["finding_id"] for finding in entries[0]["findings"]]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(ids[1].endswith("-1"))

    def test_an_existing_id_is_never_reminted(self):
        entries = [{"tool": "a", "findings": [_finding("R1", "t")]}]
        entries[0]["findings"][0]["finding_id"] = "carried-id"
        cf.mint_finding_ids(entries)
        self.assertEqual(entries[0]["findings"][0]["finding_id"], "carried-id")

    def test_the_title_is_normalized_before_hashing(self):
        """Whitespace and case churn in the title must not mint a new id."""
        one = [{"tool": "a", "findings": [_finding("R1", "A  Title")]}]
        two = [{"tool": "a", "findings": [_finding("R1", "a title")]}]
        cf.mint_finding_ids(one)
        cf.mint_finding_ids(two)
        self.assertEqual(
            one[0]["findings"][0]["finding_id"],
            two[0]["findings"][0]["finding_id"],
        )


class BuildProvenanceTest(unittest.TestCase):

    def _provenance(self, decision, judged=None, order=("a", "b")):
        merged = cf.merge_feedback(decision, judged, list(order))
        entries = merged["findings_by_tool"]
        cf.mint_finding_ids(entries)
        return cf.build_provenance(decision, entries), entries

    def test_a_carried_run_reports_no_new_or_resolved_findings(self):
        decision = cf.decide(_context(), _JUDGE)
        provenance, _ = self._provenance(decision)
        self.assertEqual(provenance["mode"], cf.MODE_CARRIED)
        self.assertEqual(provenance["new_finding_ids"], [])
        self.assertEqual(provenance["resolved_finding_ids"], [])
        self.assertEqual(
            provenance["finding_count"], provenance["previous_finding_count"]
        )

    def test_a_partial_run_labels_every_tool_by_where_it_came_from(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        judged = {
            "findings_by_tool": [
                {"tool": "b", "findings": [_finding("R3", "fresh b")]}
            ],
            "waived": [],
            "summary": "",
        }
        provenance, _ = self._provenance(decision, judged)
        self.assertEqual(
            provenance["tool_provenance"], {"a": "carried", "b": "rejudged"}
        )
        self.assertEqual(provenance["rejudged_tools"], ["b"])

    def test_a_full_judge_labels_nothing_as_carried(self):
        """Every tool was re-judged, so a per-tool marker would be a lie."""
        decision = cf.decide(_context(), "new-judge-fp")
        provenance, _ = self._provenance(
            decision,
            {"findings_by_tool": [], "waived": [], "summary": ""},
        )
        self.assertEqual(provenance["tool_provenance"], {})

    def test_a_dropped_finding_is_reported_as_resolved(self):
        decision = cf.decide(_context({"a": "fp-a", "b": "EDITED"}), _JUDGE)
        judged = {
            "findings_by_tool": [],  # b's finding is gone
            "waived": [],
            "summary": "",
        }
        provenance, _ = self._provenance(decision, judged)
        self.assertEqual(len(provenance["resolved_finding_ids"]), 1)
        self.assertEqual(provenance["finding_count"], 1)

    def test_component_changes_carry_the_before_and_after_values(self):
        decision = cf.decide_with_components(
            _context(),
            "new-fp",
            {"judge_model": "new-model", "style_guide_sha": "s"},
        )
        provenance, _ = self._provenance(
            decision,
            {"findings_by_tool": [], "waived": [], "summary": ""},
        )
        self.assertEqual(
            provenance["component_changes"]["judge_model"],
            {"from": "m", "to": "new-model"},
        )

    def test_a_non_scalar_component_change_is_reported_without_values(self):
        decision = cf.decide_with_components(
            _context(),
            "new-fp",
            {
                "judge_model": "m",
                "style_guide_sha": "s",
                "exceptions": [{"rule_id": "r"}],
            },
        )
        provenance, _ = self._provenance(
            decision,
            {"findings_by_tool": [], "waived": [], "summary": ""},
        )
        self.assertEqual(provenance["component_changes"]["exceptions"], {})


if __name__ == "__main__":
    unittest.main()
