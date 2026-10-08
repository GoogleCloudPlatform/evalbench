"""Deciding what to re-judge, and merging carried findings with fresh ones.

The invariant: same endpoint, same per-tool fingerprints and same judge
fingerprint means byte-identical feedback and zero model calls. Every departure
is reported with a named reason (see CHANGE_REASONS).

Lives under scorers/ because evaluator.mcp_readability imports the orchestrator,
which imports the scorers, so Baseline is imported under TYPE_CHECKING only.
"""

from collections.abc import Mapping, Sequence
import copy
from dataclasses import dataclass, field
import hashlib
import re
from typing import TYPE_CHECKING, Any

from scorers.mcp_readability.fingerprint import component_diff

if TYPE_CHECKING:
    from evaluator.mcp_readability.baseline import Baseline


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
    ("prompt_version", PROMPT_CHANGED),
    ("prompt_sha", PROMPT_CHANGED),
    ("scorer_name", PROMPT_CHANGED),
    ("exceptions", WAIVERS_CHANGED),
    ("product_name", ENDPOINT_IDENTITY_CHANGED),
)

# The judge's entry for issues that belong to no individual tool.
GENERAL = "general"

# Severity order within a tool's entry, as the judge is asked to emit it.
_SEVERITY_ORDER = ("P0", "P1", "P2")


@dataclass
class BaselineContext:
    """What the orchestrator hands the scorer so it can skip work.

    override_reason is set when the baseline must not be used (expiry, forced
    refresh, unreadable store); the scorer then re-judges in full and reports
    that reason.
    """

    endpoint_key: str = ""
    job_id: str = ""
    tool_fingerprints: dict[str, str] = field(default_factory=dict)
    baseline: "Baseline | None" = None
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
    judge_components: dict[str, Any] = field(default_factory=dict)
    baseline: "Baseline | None" = None

    @property
    def needs_model_call(self) -> bool:
        """Whether this run still has to call the judge."""
        return self.mode != MODE_CARRIED

    @property
    def names_changed(self) -> bool:
        """Whether the set of tool names changed, not just their contents."""
        return bool(self.added_tools or self.removed_tools)

    @property
    def accepted_tools(self) -> set[str]:
        """Per-tool entries the judge's output is trusted for.

        Excludes general, which _merge_partial decides on separately.
        """
        return set(self.rejudged_tools) | set(self.added_tools)

    @property
    def tools_to_report(self) -> list[str]:
        """What the judge is asked to report on in a partial run.

        Includes general when the tool-name set changed, since a rename or
        removal is what creates a cross-tool finding.
        """
        names = self.accepted_tools
        if self.names_changed:
            names.add(GENERAL)
        return sorted(names)


def _full_judge(context: BaselineContext, change_reason: str) -> Decision:
    """Re-judge every tool, keeping the baseline as the comparison point."""
    return Decision(
        mode=MODE_FULL_JUDGE,
        change_reason=change_reason,
        rejudged_tools=list(context.tool_fingerprints or {}),
        baseline=context.baseline,
    )


def decide(context: BaselineContext | None, judge_fingerprint: str) -> Decision:
    """Choose full / partial / carried for one endpoint."""
    if context is None:
        return Decision(mode=MODE_FULL_JUDGE, change_reason=NO_BASELINE)

    baseline = context.baseline
    if context.override_reason:
        # Findings are not reused, but the baseline is still the reference for
        # what changed since last time.
        return _full_judge(context, context.override_reason)
    if baseline is None:
        return _full_judge(context, NO_BASELINE)
    if judge_fingerprint != baseline.judge_fingerprint:
        return _full_judge(context, PROMPT_CHANGED)

    current_fingerprints = context.tool_fingerprints or {}
    previous_fingerprints = baseline.tool_fingerprints or {}
    added = [
        tool
        for tool in current_fingerprints
        if tool not in previous_fingerprints
    ]
    removed = [
        tool
        for tool in previous_fingerprints
        if tool not in current_fingerprints
    ]
    changed = [
        tool
        for tool in current_fingerprints
        if tool in previous_fingerprints
        and current_fingerprints[tool] != previous_fingerprints[tool]
    ]
    unchanged = [
        tool
        for tool in current_fingerprints
        if tool in previous_fingerprints
        and current_fingerprints[tool] == previous_fingerprints[tool]
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
    judge_components: Mapping[str, Any],
) -> Decision:
    """decide(), with the component diff computed against context."""
    decision = decide(context, judge_fingerprint)
    decision.judge_components = dict(judge_components or {})
    baseline = context.baseline if context else None
    if (
        baseline is not None
        and decision.mode == MODE_FULL_JUDGE
        and not context.override_reason
    ):
        if not baseline.judge_components:
            # Diffing against {} would report every key as changed, so say
            # plainly that it could not be compared.
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


def _reason_for_components(diff: Sequence[str]) -> str:
    """Map a component diff to the single reason reported for the run."""
    for component, reason in _COMPONENT_REASONS:
        if component in diff:
            return reason
    if diff:
        # A component that predates this mapping; the superset is accurate.
        return PROMPT_CHANGED
    # The fingerprint differs but no individual component does. A baseline
    # exists, so this is not a first run -- it simply cannot be compared.
    return BASELINE_UNAVAILABLE


def unreported_findings(
    decision: Decision, judged: Mapping[str, Any] | None
) -> list[dict[str, Any]]:
    """Baseline findings on re-judged tools that this run's judge did not repeat.

    Either the change resolved the issue or the judge did not mention it, so
    they go to a second, narrower pass. Returned in findings_by_tool shape to
    feed straight back into merge_feedback.
    """
    baseline = decision.baseline
    if decision.mode != MODE_PARTIAL or baseline is None:
        return []

    scope = set(decision.rejudged_tools)
    if decision.names_changed:
        scope.add(GENERAL)
    reported = set(_by_id(_entries(judged or {})))

    unreported = []
    for entry in _entries(baseline.feedback or {}):
        if entry["tool"] not in scope:
            continue
        missing = [
            finding
            for finding in entry["findings"]
            if finding.get("finding_id") not in reported
        ]
        if missing:
            unreported.append({"tool": entry["tool"], "findings": missing})
    return unreported


def merge_feedback(
    decision: Decision,
    judged: Mapping[str, Any] | None,
    tool_order: Sequence[str],
    restored: Sequence[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Combine carried baseline findings with the judge's fresh ones.

    judged is None in carried mode. restored holds findings the reconciliation
    pass ruled still apply; they keep their original wording. Entries follow
    man-page order with general first. The caller recomputes the counts.
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

    if restored:
        _restore(merged["findings_by_tool"], restored)
    merged["findings_by_tool"] = _ordered(merged["findings_by_tool"], tool_order)
    return merged


def _restore(
    entries: list[dict[str, Any]], restored: Sequence[dict[str, Any]]
) -> None:
    """Put reconciled findings back under their tool, in place.

    A tool whose every finding went unreported has no entry to rejoin, so one
    is created. Touched entries are re-sorted so a restored P0 does not
    trail a P2.
    """
    by_tool = {entry["tool"]: entry for entry in entries}
    for entry in restored:
        findings = [
            finding
            for finding in entry.get("findings", [])
            if isinstance(finding, dict)
        ]
        if not findings:
            continue
        target = by_tool.get(entry["tool"])
        if target is None:
            target = {"tool": entry["tool"], "findings": []}
            by_tool[entry["tool"]] = target
            entries.append(target)
        known = {
            finding.get("finding_id") for finding in target["findings"]
        }
        target["findings"].extend(
            finding
            for finding in findings
            if finding.get("finding_id") not in known
        )
        target["findings"].sort(key=_severity_rank)


def _severity_rank(finding: Mapping[str, Any]) -> int:
    """Sort key placing P0 first and unknown severities last."""
    severity = str(finding.get("severity", "")).upper()
    if severity in _SEVERITY_ORDER:
        return _SEVERITY_ORDER.index(severity)
    return len(_SEVERITY_ORDER)


def _merge_partial(
    decision: Decision,
    judged: Mapping[str, Any],
    baseline_feedback: Mapping[str, Any],
) -> dict[str, Any]:
    """Accept judge entries only for changed or added tools; carry the rest.

    Filtering here, not the prompt, is what provides the guarantee; the prompt
    clause only trims output tokens.
    """
    accept = decision.accepted_tools
    carry = set(decision.carried_tools)
    baseline_entries = _entries(baseline_feedback)
    judged_entries = _entries(judged)

    merged_entries = []
    for entry in judged_entries:
        if entry["tool"] in accept:
            merged_entries.append(entry)
    _stabilize(merged_entries, baseline_entries)
    for entry in baseline_entries:
        if entry["tool"] in carry:
            merged_entries.append(entry)

    # A fresh "general" entry is only trustworthy when the set of tool names
    # changed, since a rename, addition or removal is what creates a cross-tool
    # inconsistency. When names did change it is re-judged, so its absence is
    # ambiguous and goes to the second pass rather than being read as fixed.
    general = [
        entry
        for entry in (
            judged_entries if decision.names_changed else baseline_entries
        )
        if entry["tool"] == GENERAL
    ]
    _stabilize(general, baseline_entries)
    merged_entries.extend(general)

    return {
        "findings_by_tool": merged_entries,
        "waived": judged.get("waived") or baseline_feedback.get("waived") or [],
        "summary": judged.get("summary", "") or baseline_feedback.get(
            "summary", ""
        ),
    }


def _stabilize(
    entries: list[dict[str, Any]], baseline_entries: Sequence[dict[str, Any]]
) -> None:
    """Give surviving findings back their previous wording, in place.

    A re-judged tool is described from scratch, so an untouched issue comes
    back reworded and the report moves for no reason. A matching finding_id
    means the same rule on the same locator, so the baseline's text is kept.
    """
    previous_by_id = _by_id(baseline_entries)
    if not previous_by_id:
        return
    for entry in entries:
        entry["findings"] = [
            previous_by_id.get(finding.get("finding_id"), finding)
            for finding in entry["findings"]
        ]


def _by_id(entries: Sequence[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """Index findings by finding_id. Ids embed the tool, so one map covers all."""
    return {
        finding["finding_id"]: finding
        for entry in entries
        for finding in entry.get("findings", [])
        if isinstance(finding, dict) and finding.get("finding_id")
    }


def _entries(feedback: Mapping[str, Any] | None) -> list[dict[str, Any]]:
    """Return the usable per-tool entries, deep-copied.

    A Baseline is shared by every caller and the merged findings are mutated
    later, so returning the store's own dicts would let one endpoint write into
    another's baseline. Entries with no findings are dropped.
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
        findings = [
            finding for finding in findings if isinstance(finding, dict)
        ]
        if findings:
            entries.append({"tool": tool, "findings": copy.deepcopy(findings)})
    return entries


def _ordered(
    entries: Sequence[dict[str, Any]], tool_order: Sequence[str]
) -> list[dict[str, Any]]:
    """Order entries: general first, then man-page order, then anything else."""
    rank = {name: i for i, name in enumerate(tool_order)}
    rank[GENERAL] = -1
    fallback = len(tool_order)
    return sorted(entries, key=lambda entry: rank.get(entry["tool"], fallback))


def mint_finding_ids(entries: list[dict[str, Any]]) -> None:
    """Assign a stable finding_id to every finding, in place.

    (tool, rule_id) is not unique, so the optional locator and then the title
    disambiguate, with an ordinal as the last resort. Carried findings keep the
    id they arrived with, so identity only holds from the first carried run.
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
            finding["finding_id"] = base if count == 0 else f"{base}-{count}"


def _finding_id(tool: str, finding: Mapping[str, Any]) -> str:
    """Hash a finding's tool, rule and discriminator into a short id."""
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
    """Collapse whitespace and case so wording churn keeps the same id."""
    return re.sub(r"\s+", " ", text).strip().lower()


def finding_ids(entries: Sequence[dict[str, Any]]) -> set[str]:
    """Return every finding_id present in a list of per-tool entries."""
    return {
        finding["finding_id"]
        for entry in entries
        for finding in entry.get("findings", [])
        if isinstance(finding, dict) and finding.get("finding_id")
    }


def build_provenance(
    decision: Decision, merged_entries: Sequence[dict[str, Any]]
) -> dict[str, Any]:
    """Build the provenance block recorded for this run and shown in HTML.

    Records where each tool's findings came from and which are new versus
    resolved. Persisted to mcp_readability_feedback_provenance_json only.
    """
    baseline = decision.baseline
    previous_entries = _entries(baseline.feedback if baseline else {})
    previous_ids = finding_ids(previous_entries)
    current_ids = finding_ids(merged_entries)

    # Markers only mean something when some tools were spared; in a full judge
    # the banner already says why everything was re-judged.
    tool_provenance = {}
    if decision.mode != MODE_FULL_JUDGE:
        for tool in decision.carried_tools:
            tool_provenance[tool] = "carried"
        for tool in decision.rejudged_tools:
            tool_provenance[tool] = "rejudged"
        for tool in decision.added_tools:
            tool_provenance[tool] = "new"

    return {
        "component_changes": _component_changes(decision),
        "mode": decision.mode,
        "change_reason": decision.change_reason,
        "baseline_job_id": baseline.job_id if baseline else "",
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


def _component_changes(decision: Decision) -> dict[str, dict[str, Any]]:
    """Return before/after values for the scalar judge components that changed.

    Non-scalar components, such as the waiver list, are reported as changed
    without their contents.
    """
    baseline = decision.baseline
    previous_components = (baseline.judge_components if baseline else {}) or {}
    current_components = decision.judge_components or {}
    changes = {}
    for component in decision.component_diff:
        before = previous_components.get(component)
        after = current_components.get(component)
        if isinstance(before, (str, int, float, type(None))) and isinstance(
            after, (str, int, float, type(None))
        ):
            changes[component] = {"from": before, "to": after}
        else:
            changes[component] = {}
    return changes


def _count(entries: Sequence[dict[str, Any]]) -> int:
    """Total findings across every entry."""
    return sum(len(entry.get("findings", [])) for entry in entries)
