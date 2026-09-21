"""Unit tests for the MCP readability fingerprints.

A fingerprint that is stable when it should not be would misreport an edited
tool as unchanged, and one that churns would report every run as a rewrite.
The same holds for the judge fingerprint over the prompt's other inputs.
"""

import unittest

from mcp import types as mcp_types

from scorers.mcp_fingerprint import (
    canonical_exceptions,
    component_diff,
    judge_fingerprint,
    tool_fingerprints,
    toolset_fingerprint,
)


def _tool(name, description="Does a thing.", schema=None):
    return mcp_types.Tool(
        name=name,
        description=description,
        inputSchema=schema or {"type": "object"},
    )


class ToolFingerprintsTest(unittest.TestCase):

    def test_identical_tools_fingerprint_identically(self):
        self.assertEqual(
            tool_fingerprints([_tool("a")]), tool_fingerprints([_tool("a")])
        )

    def test_description_change_changes_the_fingerprint(self):
        before = tool_fingerprints([_tool("a", "Old.")])
        after = tool_fingerprints([_tool("a", "New.")])
        self.assertNotEqual(before["a"], after["a"])

    def test_schema_change_changes_the_fingerprint(self):
        before = tool_fingerprints([_tool("a")])
        after = tool_fingerprints(
            [_tool("a", schema={"type": "object",
                                "properties": {"x": {"type": "string"}}})]
        )
        self.assertNotEqual(before["a"], after["a"])

    def test_other_tools_do_not_affect_a_tool_fingerprint(self):
        alone = tool_fingerprints([_tool("a")])
        together = tool_fingerprints([_tool("a"), _tool("b")])
        self.assertEqual(alone["a"], together["a"])

    def test_duplicate_names_keep_the_first_declaration(self):
        fps = tool_fingerprints([_tool("a", "First."), _tool("a", "Second.")])
        self.assertEqual(list(fps), ["a"])
        self.assertEqual(fps["a"], tool_fingerprints([_tool("a", "First.")])["a"])


class ToolsetFingerprintTest(unittest.TestCase):

    def test_reordering_tools_does_not_change_the_toolset_fingerprint(self):
        forward = tool_fingerprints([_tool("a"), _tool("b")])
        backward = tool_fingerprints([_tool("b"), _tool("a")])
        self.assertNotEqual(list(forward), list(backward))  # order differs
        self.assertEqual(
            toolset_fingerprint(forward), toolset_fingerprint(backward)
        )

    def test_adding_a_tool_changes_the_toolset_fingerprint(self):
        self.assertNotEqual(
            toolset_fingerprint(tool_fingerprints([_tool("a")])),
            toolset_fingerprint(tool_fingerprints([_tool("a"), _tool("b")])),
        )

    def test_renaming_a_tool_changes_the_toolset_fingerprint(self):
        self.assertNotEqual(
            toolset_fingerprint(tool_fingerprints([_tool("a")])),
            toolset_fingerprint(tool_fingerprints([_tool("z")])),
        )


class CanonicalExceptionsTest(unittest.TestCase):

    def test_reordering_waivers_is_not_a_change(self):
        one = [{"rule_id": "b", "reason": "y"}, {"rule_id": "a", "reason": "x"}]
        two = [{"rule_id": "a", "reason": "x"}, {"rule_id": "b", "reason": "y"}]
        self.assertEqual(canonical_exceptions(one), canonical_exceptions(two))

    def test_extra_keys_are_dropped(self):
        self.assertEqual(
            canonical_exceptions([{"rule_id": "a", "reason": "x", "note": "z"}]),
            [{"rule_id": "a", "reason": "x"}],
        )

    def test_non_dict_entries_are_ignored(self):
        self.assertEqual(canonical_exceptions(["nope", None]), [])


class JudgeFingerprintTest(unittest.TestCase):

    def _components(self, **overrides):
        components = {
            "scorer_name": "mcp_style_readability",
            "prompt_version": "1",
            "style_guide_sha": "abc",
            "judge_model": "gemini-3.1-pro-preview",
            "product_name": "AlloyDB",
            "exceptions": [],
        }
        components.update(overrides)
        return components

    def test_same_components_same_fingerprint(self):
        first, _ = judge_fingerprint(self._components())
        second, _ = judge_fingerprint(self._components())
        self.assertEqual(first, second)

    def test_key_order_does_not_matter(self):
        components = self._components()
        reversed_order = dict(reversed(list(components.items())))
        self.assertEqual(
            judge_fingerprint(components)[0],
            judge_fingerprint(reversed_order)[0],
        )

    def test_each_component_change_changes_the_fingerprint(self):
        baseline, _ = judge_fingerprint(self._components())
        for key, value in [
            ("style_guide_sha", "def"),
            ("judge_model", "gemini-2.5-pro"),
            ("prompt_version", "2"),
            ("product_name", "Cloud SQL"),
            ("exceptions", [{"rule_id": "a", "reason": "x"}]),
        ]:
            with self.subTest(component=key):
                changed, _ = judge_fingerprint(self._components(**{key: value}))
                self.assertNotEqual(baseline, changed)

    def test_components_are_returned_for_diffing(self):
        _, components = judge_fingerprint(self._components())
        self.assertEqual(components["judge_model"], "gemini-3.1-pro-preview")


class ComponentDiffTest(unittest.TestCase):

    def test_names_only_the_changed_components(self):
        self.assertEqual(
            component_diff(
                {"judge_model": "a", "style_guide_sha": "s"},
                {"judge_model": "b", "style_guide_sha": "s"},
            ),
            ["judge_model"],
        )

    def test_added_and_removed_components_count_as_changed(self):
        self.assertEqual(component_diff({}, {"judge_model": "a"}), ["judge_model"])
        self.assertEqual(component_diff({"judge_model": "a"}, {}), ["judge_model"])

    def test_identical_components_have_no_diff(self):
        self.assertEqual(component_diff({"a": 1}, {"a": 1}), [])


if __name__ == "__main__":
    unittest.main()
