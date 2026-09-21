"""Deciding what to re-judge, and merging carried findings with fresh ones.

The invariant: given the same endpoint, the same per-tool fingerprints and the
same judge fingerprint, the emitted feedback is byte-identical to the previous
run and zero model calls are made. Every departure from it is reported with a
named reason (see CHANGE_REASONS), derived from an exact set-difference over
fingerprint components rather than inferred.

The judge already groups its findings per tool, so the skip is per tool too:
only tools whose rendered surface changed are re-judged, and a team's numbers
move for the tools they actually touched.

This sits under scorers/ rather than evaluator/mcp_readability/ because that
package's __init__ imports the orchestrator, which imports the scorers, so a
scorer importing from it would be a circular import. That is also why a
baseline is taken structurally here rather than imported from the store that
loads it: these functions only read its attributes.
"""

import copy
from dataclasses import dataclass, field
import hashlib
import re
from typing import Any

from scorers.mcp_fingerprint import component_diff


# How this run's feedback was produced.
MODE_FULL_JUDGE = "full_judge"  # every tool judged
MODE_PARTIAL = "partial"  # some tools judged, the rest carried
MODE_CARRIED = "carried"  # nothing judged, no model call


# Why this run's feedback may differ from the previous one. Exactly one is
# reported per endpoint, in mcp_readability_change_reason.
UNCHANGED = "unchanged"
TOOLS_CHANGED = "tools_changed"
STYLE_GUIDE_CHANGED = "style_guide_changed"
MODEL_CHANGED = "model_changed"
WAIVERS_CHANGED = "waivers_changed"
PROMPT_CHANGED = "prompt_changed"
BASELINE_EXPIRED = "baseline_expired"
FORCED_REFRESH = "forced_refresh"
ENDPOINT_IDENTITY_CHANGED = "endpoint_identity_changed"
BASELINE_UNAVAILABLE = "baseline_unavailable"
NO_BASELINE = "no_baseline"

CHANGE_REASONS = (
    UNCHANGED,
    TOOLS_CHANGED,
    STYLE_GUIDE_CHANGED,
    MODEL_CHANGED,
    WAIVERS_CHANGED,
    PROMPT_CHANGED,
    BASELINE_EXPIRED,
    FORCED_REFRESH,
    ENDPOINT_IDENTITY_CHANGED,
    BASELINE_UNAVAILABLE,
    NO_BASELINE,
)


# Which judge component maps to which reported reason. Ordered: when several
# components change at once the earliest match wins, so the most consequential
# (and most likely to explain a count swing) is the one reported.
_COMPONENT_REASONS = (
    ("judge_model", MODEL_CHANGED),
    ("style_guide_sha", STYLE_GUIDE_CHANGED),
    ("style_guide_path", STYLE_GUIDE_CHANGED),
    ("prompt_version", PROMPT_CHANGED),
    ("prompt_sha", PROMPT_CHANGED),
    ("scorer_name", PROMPT_CHANGED),
    ("exceptions", WAIVERS_CHANGED),
    ("product_name", ENDPOINT_IDENTITY_CHANGED),
)

# The judge's entry for issues that belong to no individual tool.
GENERAL = "general"


@dataclass
class BaselineContext:
    """What the orchestrator hands the scorer so it can skip work.

    Carried on EndpointContext as a single baseline field. override_reason is
    set when the orchestrator already knows the baseline must not be used
    (expiry, a forced refresh, or a store that could not be read); the scorer
    then re-judges in full and reports that reason instead of guessing at one.
    """

    endpoint_key: str = ""
    job_id: str = ""
    tool_fingerprints: dict = field(default_factory=dict)
    toolset_fingerprint: str = ""
    # An evaluator.mcp_readability.baseline.Baseline, or None when the
    # store had nothing for this endpoint. Untyped to avoid the import
    # cycle described above.
    baseline: Any = None
    override_reason: str = ""


@dataclass
class Decision:
    """What to judge for one endpoint, and how to explain the outcome."""

    mode: str = MODE_FULL_JUDGE
    change_reason: str = NO_BASELINE
    rejudged_tools: list[str] = field(default_factory=list)
    carried_tools: list[str] = field(default_factory=list)
    added_tools: list[str] = field(default_factory=list)
    removed_tools: list[str] = field(default_factory=list)
    component_diff: list[str] = field(default_factory=list)
    judge_components: dict = field(default_factory=dict)
    baseline: Any = None

    @property
    def needs_model_call(self) -> bool:
        return self.mode != MODE_CARRIED

    @property
    def names_changed(self) -> bool:
        """Whether the set of tool names changed, not just their contents.

        A rename, addition or removal is what creates a cross-tool
        inconsistency, so this decides whether the general entry is re-derived
        or carried.
        """
        return bool(self.added_tools or self.removed_tools)

    @property
    def tools_to_report(self) -> list[str]:
        """What the judge is asked to report on in a partial run.

        Includes general when the tool-name set changed. The focus clause tells
        the judge to emit entries only for the names listed here, so omitting it
        would suppress the cross-tool findings a rename or removal is most
        likely to create, and _merge_partial would then source general from an
        empty judged response and lose the baseline's copy too.
        """
        names = set(self.rejudged_tools) | set(self.added_tools)
        if self.names_changed:
            names.add(GENERAL)
        return sorted(names)

    @property
    def accepted_tools(self) -> set[str]:
        """Per-tool entries the judge's output is trusted for.

        Excludes general, which _merge_partial decides on separately.
        """
        return set(self.rejudged_tools) | set(self.added_tools)


def decide(context: BaselineContext | None, judge_fingerprint: str) -> Decision:
    """Choose full / partial / carried for one endpoint."""
    if context is None:
        return Decision(mode=MODE_FULL_JUDGE, change_reason=NO_BASELINE)

    current = context.tool_fingerprints or {}
    if context.override_reason:
        # The baseline stays attached even though its findings are not reused:
        # it is still the reference for "what changed since last time".
        # Dropping it would report every finding as new and blank the date in
        # the banner, on exactly the runs (expiry, forced refresh) where the
        # reader most needs to know what moved.
        return Decision(
            mode=MODE_FULL_JUDGE,
            change_reason=context.override_reason,
            rejudged_tools=list(current),
            baseline=context.baseline,
        )

    baseline = context.baseline
    if baseline is None:
        return Decision(
            mode=MODE_FULL_JUDGE,
            change_reason=NO_BASELINE,
            rejudged_tools=list(current),
        )

    if judge_fingerprint != baseline.judge_fingerprint:
        # A changed judge invalidates every finding, not just the ones for
        # changed tools: the same tool can be judged differently by a new model
        # or against a new guide. decide_with_components names which input
        # changed; on its own decide() can only say that one did.
        return Decision(
            mode=MODE_FULL_JUDGE,
            change_reason=PROMPT_CHANGED,
            rejudged_tools=list(current),
            baseline=baseline,
        )

    previous = baseline.tool_fingerprints or {}
    added = [name for name in current if name not in previous]
    removed = [name for name in previous if name not in current]
    changed = [
        name
        for name in current
        if name in previous and current[name] != previous[name]
    ]
    unchanged = [
        name
        for name in current
        if name in previous and current[name] == previous[name]
    ]

    if not added and not removed and not changed:
        return Decision(
            mode=MODE_CARRIED,
            change_reason=UNCHANGED,
            carried_tools=unchanged,
            baseline=baseline,
        )

    return Decision(
        mode=MODE_PARTIAL,
        change_reason=TOOLS_CHANGED,
        rejudged_tools=changed,
        carried_tools=unchanged,
        added_tools=added,
        removed_tools=removed,
        baseline=baseline,
    )


def decide_with_components(
    context: BaselineContext | None,
    judge_fingerprint: str,
    judge_components: dict,
) -> Decision:
    """decide(), with the component diff computed against context.

    Split out so decide() stays testable with a fingerprint alone. This is what
    the scorer calls.
    """
    decision = decide(context, judge_fingerprint)
    decision.judge_components = dict(judge_components or {})
    baseline = context.baseline if context else None
    if (
        baseline is not None
        and decision.mode == MODE_FULL_JUDGE
        and not context.override_reason
    ):
        if not baseline.judge_components:
            # A baseline written before components were recorded, or one whose
            # component JSON did not parse. Diffing against {} would report
            # every key as changed and blame whichever happens to be first in
            # _COMPONENT_REASONS, so say plainly that it could not be compared.
            decision.component_diff = []
            decision.change_reason = BASELINE_UNAVAILABLE
        else:
            decision.component_diff = component_diff(
                baseline.judge_components, judge_components
            )
            decision.change_reason = _reason_for_components(
                decision.component_diff
            )
    return decision


def _reason_for_components(diff: list[str]) -> str:
    for component, reason in _COMPONENT_REASONS:
        if component in diff:
            return reason
    if diff:
        # Some component changed that predates this mapping; "the prompt inputs
        # changed" is the accurate superset.
        return PROMPT_CHANGED
    # The fingerprint differs but no individual component does. A baseline
    # exists, so this is not a first run -- it simply cannot be compared.
    return BASELINE_UNAVAILABLE


def merge_feedback(
    decision: Decision,
    judged: dict | None,
    tool_order: list[str],
) -> dict:
    """Combine carried baseline findings with the judge's fresh ones.

    judged is None in carried mode, where no model call was made. Merged entries
    follow the current man-page order with the general entry first, so the
    output ordering does not depend on which tools happened to be re-judged.

    Returns findings_by_tool, waived and summary. Counts are left out; the
    caller recomputes them over the merged findings.
    """
    baseline = decision.baseline
    baseline_feedback = (baseline.feedback if baseline else {}) or {}

    if decision.mode == MODE_CARRIED:
        merged = {
            "findings_by_tool": _entries(baseline_feedback),
            "waived": baseline_feedback.get("waived") or [],
            "summary": baseline_feedback.get("summary", ""),
        }
    elif decision.mode == MODE_PARTIAL:
        merged = _merge_partial(decision, judged or {}, baseline_feedback)
    else:
        merged = {
            "findings_by_tool": _entries(judged or {}),
            "waived": (judged or {}).get("waived") or [],
            "summary": (judged or {}).get("summary", ""),
        }

    merged["findings_by_tool"] = _ordered(merged["findings_by_tool"], tool_order)
    return merged


def _merge_partial(
    decision: Decision, judged: dict, baseline_feedback: dict
) -> dict:
    """Accept judge entries only for changed or added tools; carry the rest.

    The judge is shown the whole man page in a partial run, because its severity
    calibration is relative to the full surface and judging a lone tool inflates
    severity. It is merely asked to report on the changed ones. Filtering here,
    not the prompt, is what provides the guarantee; the prompt clause only trims
    output tokens.
    """
    accept = decision.accepted_tools
    carry = set(decision.carried_tools)

    merged_entries = []
    for entry in _entries(judged):
        if entry["tool"] in accept:
            merged_entries.append(entry)
    for entry in _entries(baseline_feedback):
        if entry["tool"] in carry:
            merged_entries.append(entry)

    # A fresh "general" entry is only trustworthy when the set of tool names
    # changed, since a rename, addition or removal is what creates a cross-tool
    # inconsistency. A description-only edit that introduces one is a known
    # false negative.
    #
    # When names did change, "general" is on the focus list, so the judge
    # omitting it means there is no cross-tool issue, the same reading given to
    # any re-judged tool that returns nothing. The dropped finding shows up in
    # the provenance block's resolved list rather than vanishing quietly.
    source = judged if decision.names_changed else baseline_feedback
    merged_entries.extend(e for e in _entries(source) if e["tool"] == GENERAL)

    return {
        "findings_by_tool": merged_entries,
        "waived": judged.get("waived") or baseline_feedback.get("waived") or [],
        "summary": judged.get("summary", "") or baseline_feedback.get(
            "summary", ""
        ),
    }


def _entries(feedback: dict) -> list[dict]:
    """Return the usable per-tool entries, deep-copied.

    The copy matters: a Baseline loaded from the store is shared by every
    caller, and the merged findings are mutated afterwards when ids are minted
    into them. Returning the store's own dicts would let one endpoint's merge
    write into another's baseline.

    Entries with no findings are dropped, matching the renderer's filter, so the
    JSON column cannot carry an entry the HTML never shows.
    """
    raw = (feedback or {}).get("findings_by_tool")
    if not isinstance(raw, list):
        return []
    entries = []
    for entry in raw:
        if not isinstance(entry, dict):
            continue
        tool = str(entry.get("tool", "")).strip()
        findings = entry.get("findings")
        if not tool or not isinstance(findings, list):
            continue
        findings = [f for f in findings if isinstance(f, dict)]
        if findings:
            entries.append({"tool": tool, "findings": copy.deepcopy(findings)})
    return entries


def _ordered(entries: list[dict], tool_order: list[str]) -> list[dict]:
    """Order entries: general first, then man-page order, then anything else."""
    rank = {name: i for i, name in enumerate(tool_order)}
    fallback = len(rank)

    def key(item):
        index, entry = item
        if entry["tool"] == GENERAL:
            return (-1, index)
        return (rank.get(entry["tool"], fallback), index)

    return [entry for _, entry in sorted(enumerate(entries), key=key)]


def mint_finding_ids(entries: list[dict]) -> None:
    """Assign a stable finding_id to every finding, in place.

    (tool, rule_id) is not unique, since the prompt invites the same rule to be
    reported several times for one tool, so the optional locator (a parameter
    name, description, name) and then the title disambiguate, with an ordinal
    suffix as the last resort.

    Ids are minted before the delta view that consumes them ships. Carried
    findings keep whatever id they arrived with, so identity is only stable if
    ids exist from the first generation that gets carried; retrofitting later
    leaves every existing baseline without them.
    """
    seen: dict[str, int] = {}
    for entry in entries:
        tool = entry.get("tool", "")
        for finding in entry.get("findings", []):
            if not isinstance(finding, dict) or finding.get("finding_id"):
                continue
            base = _finding_id(tool, finding)
            count = seen.get(base, 0)
            seen[base] = count + 1
            finding["finding_id"] = base if not count else f"{base}-{count}"


def _finding_id(tool: str, finding: dict) -> str:
    discriminator = finding.get("locator") or finding.get("title") or ""
    raw = "|".join(
        [
            str(tool),
            str(finding.get("rule_id", "")),
            _normalize(str(discriminator)),
        ]
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:12]


def _normalize(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip().lower()


def finding_ids(entries: list[dict]) -> set[str]:
    """Return every finding_id present in a list of per-tool entries."""
    return {
        finding["finding_id"]
        for entry in entries
        for finding in entry.get("findings", [])
        if isinstance(finding, dict) and finding.get("finding_id")
    }


def build_provenance(
    decision: Decision, merged_entries: list[dict], job_id: str = ""
) -> dict[str, Any]:
    """Build the provenance block stored with the feedback and shown in HTML.

    Records where each tool's findings came from, which findings are new versus
    resolved, and how old the oldest carried judgement is, so a finding carried
    for 200 days without ever being re-derived is visible rather than silently
    authoritative.

    job_id is this run's, used as the origin when findings were re-derived. The
    result is stored both inside the feedback JSON column and in
    mcp_readability_feedback_provenance_json.
    """
    baseline = decision.baseline
    previous_entries = _entries(baseline.feedback if baseline else {})
    previous_ids = finding_ids(previous_entries)
    current_ids = finding_ids(merged_entries)

    # Per-tool markers only mean something when some tools were spared: in a
    # full judge every tool was re-judged for a reason the banner already
    # states, and labelling them all "schema changed" would be a lie.
    tool_provenance = {}
    if decision.mode != MODE_FULL_JUDGE:
        for name in decision.carried_tools:
            tool_provenance[name] = "carried"
        for name in decision.rejudged_tools:
            tool_provenance[name] = "rejudged"
        for name in decision.added_tools:
            tool_provenance[name] = "new"

    return {
        "component_changes": _component_changes(decision),
        "mode": decision.mode,
        "change_reason": decision.change_reason,
        "baseline_job_id": baseline.job_id if baseline else "",
        "baseline_origin_job_id": _origin_job_id(baseline, decision, job_id),
        "baseline_timestamp": baseline.check_timestamp if baseline else "",
        "rejudged_tools": sorted(decision.rejudged_tools),
        "carried_tools": sorted(decision.carried_tools),
        "added_tools": sorted(decision.added_tools),
        "removed_tools": sorted(decision.removed_tools),
        "component_diff": decision.component_diff,
        "tool_provenance": tool_provenance,
        "new_finding_ids": sorted(current_ids - previous_ids),
        "resolved_finding_ids": sorted(previous_ids - current_ids),
        "previous_finding_count": _count(previous_entries),
        "finding_count": _count(merged_entries),
    }


def _component_changes(decision: Decision) -> dict[str, dict]:
    """Return before/after values for the scalar judge components that changed.

    The diff alone gives "the model changed"; the values give
    "gemini-2.5-pro -> gemini-3.1-pro-preview", which is the difference between
    a reader trusting the explanation and going looking for it. Non-scalar
    components such as the waiver list are reported as changed without their
    contents.
    """
    baseline = decision.baseline
    previous = (baseline.judge_components if baseline else {}) or {}
    current = decision.judge_components or {}
    changes = {}
    for name in decision.component_diff:
        before, after = previous.get(name), current.get(name)
        if isinstance(before, (str, int, float, type(None))) and isinstance(
            after, (str, int, float, type(None))
        ):
            changes[name] = {"from": before, "to": after}
        else:
            changes[name] = {}
    return changes


def _origin_job_id(baseline, decision: Decision, job_id: str) -> str:
    """Return the oldest generation still represented in these findings.

    A fully carried run inherits whatever the baseline recorded, so the chain
    points transitively back at the first full judge. A partial run inherits it
    too, because some of its findings really are that old, and reporting the
    current job would hide the entrenchment this column exists to expose. Only a
    run that re-derived everything becomes its own origin.
    """
    if baseline is None or not decision.carried_tools:
        return job_id
    return (baseline.provenance or {}).get("baseline_origin_job_id") or (
        baseline.job_id
    )


def _count(entries: list[dict]) -> int:
    return sum(len(entry.get("findings", [])) for entry in entries)
