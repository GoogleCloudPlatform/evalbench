"""LLM-backed scorer that evaluates an MCP tool manifest against a style guide.

Follows the same shape as :class:`scorers.llmrater.LLMRater`: constructed with
``(config, global_models)`` and holds an LLM obtained via
``generators.models.get_generator``. The orchestrator calls :meth:`evaluate`
per endpoint with the tool man-page markup.

The model reviews the tools from an LLM-agent-consumption perspective and returns
a strict JSON object describing P0/P1/P2 findings, an overall readability score,
and any rules waived via the exceptions file. We parse and normalize that JSON
defensively so a slightly malformed response degrades to an ERROR row rather than
crashing the run.
"""

import html
import json
import logging
import re

from generators.models import get_generator
from scorers import mcp_carry_forward as cf
from scorers.mcp_fingerprint import (
    canonical_exceptions,
    judge_fingerprint,
    sha256_text,
)
from scorers.mcp_readability_scoring import (
    EndpointContext,
    SEVERITY_BADGES,
    ScoreContribution,
    severity_tally,
)
from util.config import load_yaml_config


# Output-token ceiling for the JSON-mode judge call. Gemini 3.x is a *thinking*
# model whose reasoning tokens count against the output budget before any JSON is
# emitted, so a generous ceiling is required or verbose findings get truncated
# mid-JSON (surfacing as a cryptic parse error). Overridable via the scorer's
# ``max_output_tokens`` config.
_MAX_OUTPUT_TOKENS = 65535


class TruncatedResponseError(Exception):
    """Raised when the model stops at the output-token limit (incomplete JSON).

    Distinct from a generation/API failure: it must NOT fall back to the plain
    ``generate()`` path (that would only re-truncate and corrupt escapes). The
    fix is a larger ``max_output_tokens``, so we surface that explicitly.
    """


# Shared JSON output contract appended to both prompts (escaped for str.format).
_OUTPUT_SCHEMA = """### OUTPUT
Return ONLY a JSON object (no markdown, no prose) with exactly this shape:
{{
  "readability_score": <integer 0-100, higher is better>,
  "findings_by_tool": [
    {{"tool": "<tool name, or 'general'>",
      "findings": [
        {{"severity": "P0|P1|P2", "rule_id": "<string>",
          "locator": "<the parameter path this is about, or 'description' /
                      'name' when it is about the tool itself; omit if none>",
          "title": "<short one-line summary>", "message": "<what is wrong>",
          "suggestion": "<how to fix>"}}
      ]}}
  ],
  "waived": [
    {{"rule_id": "<string>", "reason": "<reason>", "would_have_violated": <true|false>}}
  ],
  "summary": "<one-paragraph overall assessment>"
}}
Emit one "findings_by_tool" entry per tool that has findings, in the order the
tools appear in the man page, with the "general" entry (if any) first. Within an
entry, order findings P0, then P1, then P2. Omit tools with no findings.

Do not report issue counts: the P0/P1/P2 totals are counted from the findings
you return, one per finding."""


PROMPT_TEMPLATE = (
    """You are an expert on MCP tool design and a pragmatic
API Developer Experience reviewer. Evaluate the MCP server's tool definitions
(shown below as a man page) against the STYLE GUIDE and report every violation.

Evaluate from the perspective of an LLM agent that must call these tools, and
judge every tool against the principle of designing APIs for easy LLM
consumption (understandable terminology, simple parameters, no client-side
logic):
- Will the model understand the terminology and the tool / parameter names?
- Are the parameters too complex, too numerous, or under-described?
- Is the agent forced to act like a computer -- e.g. formatting complex strings,
  generating UUIDs, calculating timestamps, or applying other client-side logic
  -- instead of simply expressing intent?
Adopt a consultative, pragmatic tone, like a human code reviewer (e.g.
"Consider if...", "Evaluate whether..."). Do not be overly pedantic about minor
wording or text issues when larger architectural blockers exist -- prioritize
the blockers. Only report issues that genuinely apply; do not fabricate issues
for a tool that has none.

Severity levels:
- P0: blocker -- critical violation (must fix; blocks compliance).
- P1: strong recommendation -- major violation (should fix).
- P2: informal suggestion -- minor / stylistic violation (nice to fix).

How to assign severity and rule_id:
- The STYLE GUIDE annotates each rule with its priority in an HTML comment next
  to the section heading, e.g. `### Tool Names <!-- priority: p1 ... -->` or
  `#### Safe Pagination <!-- priority: p0 -->`. Use that annotated priority as
  the severity of any violation of that rule (p0 -> P0, p1 -> P1, p2 -> P2).
- A heading may specify different priorities for different aspects, e.g.
  `<!-- priority: p1 for <action>_<resource>, p2 for snake_case -->`. Honor that
  split when classifying the specific violation.
- Use the section heading text as the `rule_id` (e.g. "Tool Names",
  "Use Human-Readable Time and Durations", "Concise Descriptions").
- Only report violations of rules that actually apply to the given tools. Rules
  about platform/registration/dashboards that cannot be judged from the tool
  schema alone should not be flagged as violations.

Group findings under the tool they affect:
- Report a violation SEPARATELY for each tool it affects, under that tool's
  entry, with a "message"/"suggestion" written for that tool specifically (name
  its own parameters, description, or wording). Repeating the same rule_id under
  several tools is expected -- a rule broken by ten tools appears under all ten,
  and counts as ten findings.
- Use the "general" entry only for an issue that belongs to no individual tool:
  the server exposes too many tools, the tool set is missing a capability (e.g.
  no polling tool for a long-running operation), or a parameter for the same
  concept is named inconsistently across tools.
- Do not paper over the difference between tools: if a rule is broken in a
  different way by two tools, say what is wrong with each.
- Keep each message and suggestion to one or two sentences -- one finding per
  affected tool makes the response long.

### STYLE GUIDE
{style_guide}

### PRODUCT
{product_name}

### TOOLS (man page)
{tools_markup}

### EXCEPTIONS (waived rules — DO NOT count these as issues)
The following rules have been explicitly waived for this endpoint. Do not report
them as findings. Instead list them under "waived" with their reason. If a waived
rule would otherwise have been violated, note that in the waived entry.
{exceptions}

"""
    + _OUTPUT_SCHEMA
)


# Bumped by hand whenever a prompt edit should invalidate carried findings. The
# sha of PROMPT_TEMPLATE is fingerprinted alongside it as a backstop, so
# forgetting to bump this is safe. The constant exists to let a semantic change
# be declared even when the text is untouched.
PROMPT_VERSION = "1"


# Appended only for a partial run. The filter in scorers.mcp_carry_forward is
# what guarantees unchanged tools keep their findings; this clause only stops
# the judge spending output tokens re-describing them. The full man page is
# still supplied above, because the prompt's severity calibration ("don't be
# pedantic when architectural blockers exist") is relative to the whole surface.
_FOCUS_CLAUSE = """
### SCOPE OF THIS REVIEW
Only these tools have changed since the last review: {focus_tools}
Read the whole man page above for context -- your severity calibration must
account for the entire tool surface -- but emit "findings_by_tool" entries ONLY
for the tools listed on this line. Findings for every other tool are carried
over from the previous review and will be discarded if you repeat them.
"""


class McpStyleReadabilityScorer:
    """Scores a tools spec against the MCP style guide using an LLM."""

    # Result-row columns this scorer contributes. The trailing block records
    # what the judge was given and how much of the feedback it actually
    # produced, so a count change can be attributed without re-running anything.
    COLUMNS = [
        "mcp_readability_p0_issues",
        "mcp_readability_p1_issues",
        "mcp_readability_p2_issues",
        "mcp_readability_score",
        "mcp_readability_llm_feedback_json",
        "mcp_readability_llm_feedback_html",
        "mcp_readability_judge_fingerprint",
        "mcp_readability_judge_components_json",
        "mcp_readability_style_guide_sha",
        "mcp_readability_style_guide_path",
        "mcp_readability_prompt_version",
        "mcp_readability_judge_model",
        "mcp_readability_feedback_mode",
        "mcp_readability_change_reason",
        "mcp_readability_feedback_provenance_json",
    ]

    def __init__(self, config: dict, global_models):
        self.name = "mcp_style_readability"
        config = config or {}
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError(
                "model_config is required for the mcp_style_readability scorer"
            )
        # The scorer owns its style guide: required, read once at construction.
        style_guide_path = config.get("style_guide")
        if not style_guide_path:
            raise ValueError(
                "style_guide is required for the mcp_style_readability scorer"
            )
        self.style_guide = _read_text(style_guide_path)
        self.style_guide_path = style_guide_path
        # Only the hash is persisted, never the guide text: detection is a hash
        # comparison, and the text would be duplicated into every endpoint row
        # on every run. The path travels with it because the
        # style_guide.*.local.md overrides mean two runs can legitimately use
        # different guides, which a hash change alone cannot tell apart from an
        # edit to the same guide.
        self.style_guide_sha = sha256_text(self.style_guide)
        self.max_output_tokens = int(
            config.get("max_output_tokens", _MAX_OUTPUT_TOKENS)
        )
        self.model = get_generator(global_models, self.model_config)
        # Read the model name from the config rather than the generator object:
        # GeminiGenerator exposes vertex_model but ClaudeGenerator exposes
        # model_id, and reading the config is uniform. It also keeps the
        # google3-vs-OSS model split from letting one environment reuse the
        # other's baselines.
        self.judge_model = _judge_model_name(self.model_config)

    def run(self, context: EndpointContext) -> ScoreContribution:
        """Evaluate one endpoint: judge the man page, pass iff no P0 findings.

        Re-judges only what changed. When the tool surface and every judge input
        are unchanged since the baseline, the previous findings are returned
        verbatim and the model is never called; see scorers.mcp_carry_forward
        for the invariant this upholds.
        """
        baseline_context = getattr(context, "baseline", None)
        components = self._judge_components(context)
        fingerprint, components = judge_fingerprint(components)
        decision = cf.decide_with_components(
            baseline_context, fingerprint, components
        )
        tool_order = list(
            (baseline_context.tool_fingerprints if baseline_context else {})
            or _tool_names(context.tools)
        )

        judged = None
        if decision.needs_model_call:
            judged = self.evaluate(
                tools_markup=context.man_page,
                style_guide=self.style_guide,
                product_name=context.product_name,
                exceptions=context.exceptions,
                focus_tools=(
                    decision.tools_to_report
                    if decision.mode == cf.MODE_PARTIAL
                    else None
                ),
            )

        feedback = self._merged_feedback(
            decision,
            judged,
            tool_order,
            job_id=baseline_context.job_id if baseline_context else "",
        )
        logging.info(
            "mcp_readability: %s judged %s (%s): %d findings, %d model call(s)",
            context.product_name,
            decision.mode,
            decision.change_reason,
            feedback["p0_issues"] + feedback["p1_issues"] + feedback["p2_issues"],
            1 if decision.needs_model_call else 0,
        )

        p0 = feedback["p0_issues"]
        return ScoreContribution(
            row_fields={
                "mcp_readability_p0_issues": p0,
                "mcp_readability_p1_issues": feedback["p1_issues"],
                "mcp_readability_p2_issues": feedback["p2_issues"],
                "mcp_readability_score": int(feedback.get("readability_score", 0)),
                # Both feedback columns omit the readability score on purpose;
                # only the numeric metric column above carries it.
                "mcp_readability_llm_feedback_json": json.dumps(
                    _public_feedback(feedback)
                ),
                "mcp_readability_llm_feedback_html": self.to_html(
                    feedback, context.product_name
                ),
                "mcp_readability_judge_fingerprint": fingerprint,
                "mcp_readability_judge_components_json": json.dumps(
                    components, sort_keys=True
                ),
                "mcp_readability_style_guide_sha": self.style_guide_sha,
                "mcp_readability_style_guide_path": self.style_guide_path,
                "mcp_readability_prompt_version": PROMPT_VERSION,
                "mcp_readability_judge_model": self.judge_model,
                "mcp_readability_feedback_mode": decision.mode,
                "mcp_readability_change_reason": decision.change_reason,
                "mcp_readability_feedback_provenance_json": json.dumps(
                    feedback.get("provenance") or {}, sort_keys=True
                ),
            },
            score=100 if p0 == 0 else 0,
            logs=(
                f"p0_issues={p0}, "
                f"readability_score={feedback.get('readability_score', 0)}, "
                f"mode={decision.mode}, reason={decision.change_reason}"
            ),
        )

    def _judge_components(self, context: EndpointContext) -> dict:
        """Every judge input other than the tools themselves.

        Kept as a dict rather than a bare hash so a mismatch can name the
        component that changed, which is what turns an unexplained count swing
        into "the style guide changed".
        """
        return {
            "scorer_name": self.name,
            "prompt_version": PROMPT_VERSION,
            "prompt_sha": sha256_text(PROMPT_TEMPLATE),
            "style_guide_sha": self.style_guide_sha,
            "style_guide_path": self.style_guide_path,
            "judge_model": self.judge_model,
            # Interpolated into the prompt, so it is a judge input.
            "product_name": context.product_name or "",
            "exceptions": canonical_exceptions(context.exceptions),
        }

    def _merged_feedback(
        self, decision, judged, tool_order: list[str], job_id: str = ""
    ) -> dict:
        """Merge carried and fresh findings, then recount from the result.

        Counts are always recomputed over the merged findings, so the P0/P1/P2
        arithmetic stays consistent with what is rendered no matter how the
        findings were sourced.
        """
        merged = cf.merge_feedback(decision, judged, tool_order)
        entries = merged["findings_by_tool"]
        # Carried findings already have ids and keep them; only fresh ones are
        # minted, which is what makes finding identity stable across runs.
        cf.mint_finding_ids(entries)
        counts = _severity_counts([f for e in entries for f in e["findings"]])
        if decision.mode == cf.MODE_CARRIED and decision.baseline is not None:
            # _public_feedback strips the score from the JSON column, so it is
            # restored from the numeric metric column instead.
            score = decision.baseline.readability_score
        else:
            score = _safe_int((judged or {}).get("readability_score"))
        return {
            "readability_score": score,
            "p0_issues": counts["P0"],
            "p1_issues": counts["P1"],
            "p2_issues": counts["P2"],
            "findings_by_tool": entries,
            "waived": merged["waived"],
            "summary": merged["summary"],
            "provenance": cf.build_provenance(decision, entries, job_id),
        }

    def evaluate(
        self,
        tools_markup: str,
        style_guide: str,
        product_name: str,
        exceptions: list[dict] | None = None,
        focus_tools: list[str] | None = None,
    ) -> dict:
        """Run the LLM readability check and return a normalized feedback dict."""
        prompt = PROMPT_TEMPLATE.format(
            style_guide=style_guide or "(no style guide provided)",
            product_name=product_name or "(unknown)",
            tools_markup=tools_markup or "(no tools)",
            exceptions=json.dumps(exceptions or [], indent=2),
        )
        if focus_tools:
            prompt += _FOCUS_CLAUSE.format(focus_tools=", ".join(focus_tools))
        raw = self._generate(prompt)
        return self._parse(raw)

    def _generate(self, prompt: str) -> str:
        """Generate the model response as raw JSON text.

        Prefer Gemini's native JSON mode via the underlying genai client, which
        guarantees syntactically valid JSON and -- crucially -- bypasses
        ``GeminiGenerator.generate``'s SQL sanitizer (it strips backslashes and
        collapses whitespace, corrupting JSON escapes). Falls back to the
        generic ``generate`` for non-Gemini models.
        """
        client = getattr(self.model, "client", None)
        caller = getattr(self.model, "_call_generate_content", None)
        if client is not None and callable(caller):
            try:
                from google.genai import types

                config = types.GenerateContentConfig(
                    response_mime_type="application/json",
                    temperature=0,
                    max_output_tokens=self.max_output_tokens,
                )
                resp = caller(contents=prompt, config=config)
            except Exception as e:
                logging.warning(
                    "mcp_style_readability: JSON-mode generation failed (%s); "
                    "falling back to plain generate().",
                    e,
                )
            else:
                # A truncated response is incomplete JSON. Surface it clearly
                # instead of letting resp.text yield a partial object that dies
                # later with a cryptic parse error -- and do NOT fall back to
                # plain generate() (that would only truncate again).
                if _finish_reason_name(resp) == "MAX_TOKENS":
                    raise TruncatedResponseError(
                        "mcp_style_readability: model response was truncated at "
                        f"the output-token limit (max_output_tokens="
                        f"{self.max_output_tokens}); raise max_output_tokens for "
                        "this scorer. Note Gemini 3.x reasoning tokens count "
                        "against this budget."
                    )
                text = getattr(resp, "text", None)
                if text:
                    return text
        return self.model.generate(prompt)

    # ------------------------------------------------------------------
    # parsing / rendering
    # ------------------------------------------------------------------
    @staticmethod
    def _extract_json(text: str) -> dict:
        """Pull a JSON object out of a model response (handles code fences)."""
        if not text:
            raise ValueError("empty model response")
        text = text.strip()
        # Strip ```json ... ``` or ``` ... ``` fences (tolerating trailing ws).
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fallback: grab the outermost {...} span.
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end > start:
                try:
                    return json.loads(text[start:end + 1])
                except json.JSONDecodeError:
                    pass
            raise ValueError("no JSON object found in model response")

    def _parse(self, raw: str) -> dict:
        """Normalize the model output into a stable feedback dict."""
        data = self._extract_json(raw)
        by_tool = _clean_findings_by_tool(data.get("findings_by_tool"))
        # Mint ids here so a finding is identifiable the moment it exists: a
        # carried finding keeps the id it was born with, which is only stable if
        # every generation that can be carried has one.
        cf.mint_finding_ids(by_tool)
        counts = _severity_counts(
            [f for entry in by_tool for f in entry["findings"]]
        )
        return {
            "readability_score": _safe_int(data.get("readability_score")),
            "p0_issues": counts["P0"],
            "p1_issues": counts["P1"],
            "p2_issues": counts["P2"],
            "findings_by_tool": by_tool,
            "waived": data.get("waived") or [],
            "summary": data.get("summary", ""),
        }

    @staticmethod
    def to_html(feedback: dict, product_name: str = "") -> str:
        """Render feedback as a human-readable HTML fragment.

        Leads with a provenance banner and the overall summary, renders the
        per-tool findings in man-page order with the cross-tool general entry
        first, and ends with the allowed exceptions (waived rules) and their
        reasons. That order is the one the judge is asked for, now enforced
        rather than assumed, since a partial run assembles entries from two
        sources. No numeric readability score is shown: the intent is review
        notes an engineer can act on, not a grade.

        HTML (rather than Markdown) because this column is surfaced in a
        dashboard that renders it as HTML. All model-supplied text is escaped.
        """
        if not feedback:
            return ""

        esc = html.escape
        title = esc(str(product_name).strip() or "MCP endpoint")
        parts = [
            "<div class='mcp-readability'>",
            f"<h3>MCP Tool Readability Review — {title}</h3>",
        ]

        # Immediately after the title, before the summary: the reader's first
        # question about a changed number is "why", and an explicit "identical
        # to the previous run" builds as much trust as an explanation does.
        provenance = feedback.get("provenance") or {}
        banner = _provenance_banner(feedback)
        if banner:
            parts.append(
                "<p class='mcp-provenance'><b>Change tracking:</b> "
                f"{esc(banner)}</p>"
            )

        summary = str(feedback.get("summary", "")).strip()
        if summary:
            parts.append(f"<p><b>Summary:</b> {esc(summary)}</p>")

        tool_provenance = provenance.get("tool_provenance") or {}
        by_tool = _clean_findings_by_tool(feedback.get("findings_by_tool"))
        if not by_tool:
            parts.append("<p><i>No findings</i></p>")
        for entry in by_tool:
            items = entry["findings"]
            marker = _TOOL_MARKERS.get(tool_provenance.get(entry["tool"]), "")
            suffix = f" · {esc(marker)}" if marker else ""
            parts.append(
                f"<h4>{esc(entry['tool'])} — {severity_tally(items)}"
                f"{suffix}</h4>"
            )
            parts.append("<ul>")
            for f in items:
                rule = esc(str(f.get("rule_id", "")).strip() or "(rule)")
                sev = str(f.get("severity", "")).upper()
                badge = esc(SEVERITY_BADGES.get(sev, sev or "?"))
                li = [f"<b>{badge} · [{rule}]</b>"]
                finding_title = str(f.get("title", "")).strip()
                if finding_title:
                    li.append(f" — {esc(finding_title)}")
                message = str(f.get("message", "")).strip()
                if message:
                    li.append(f"<br><i>Issue:</i> {esc(message)}")
                suggestion = str(f.get("suggestion", "")).strip()
                if suggestion:
                    li.append(f"<br><i>Suggestion:</i> {esc(suggestion)}")
                parts.append("<li>" + "".join(li) + "</li>")
            parts.append("</ul>")

        # Allowed exceptions: the waived rules the reviewer must NOT treat as
        # violations, with the reason and whether the tools would otherwise have
        # tripped the rule.
        waived = [w for w in (feedback.get("waived") or []) if isinstance(w, dict)]
        parts.append(f"<h4>✅ Allowed exceptions (waived) — {len(waived)}</h4>")
        if not waived:
            parts.append("<p><i>None</i></p>")
        else:
            parts.append("<ul>")
            for w in waived:
                rule = esc(str(w.get("rule_id", "")).strip() or "(rule)")
                reason = esc(str(w.get("reason", "")).strip() or "no reason given")
                entry = f"<b>{rule}</b> — {reason}"
                if "would_have_violated" in w:
                    flag = "yes" if w.get("would_have_violated") else "no"
                    entry += f" <i>(would have been flagged: {flag})</i>"
                parts.append(f"<li>{entry}</li>")
            parts.append("</ul>")

        parts.append("</div>")
        return "".join(parts)


# Per-tool heading suffix. An untouched tool says so explicitly, which is what
# lets a reader tell "not re-judged" apart from "re-judged and happened to
# score the same".
_TOOL_MARKERS = {
    "carried": "unchanged since the previous review",
    "rejudged": "re-judged (schema changed)",
    "new": "new tool",
}


def _provenance_banner(feedback: dict) -> str:
    """One sentence explaining why this report differs from the previous one."""
    provenance = feedback.get("provenance") or {}
    mode = provenance.get("mode")
    if not mode:
        return ""

    reason = provenance.get("change_reason", "")
    since = _date_of(provenance.get("baseline_timestamp", ""))
    since_clause = f" since {since}" if since else ""
    previous = provenance.get("previous_finding_count", 0)
    current = provenance.get("finding_count", 0)
    new = len(provenance.get("new_finding_ids") or [])
    resolved = len(provenance.get("resolved_finding_ids") or [])
    net = (
        f" Net {previous} → {current} ({new} new, {resolved} resolved)."
        if previous or current
        else ""
    )

    if mode == cf.MODE_CARRIED:
        tally = severity_tally(
            [
                f
                for entry in _clean_findings_by_tool(
                    feedback.get("findings_by_tool")
                )
                for f in entry["findings"]
            ]
        )
        return (
            f"Unchanged{since_clause}. No tool schema changes; findings carried "
            f"forward verbatim. {tally} — identical to the previous run."
        )

    if mode == cf.MODE_PARTIAL:
        rejudged = provenance.get("rejudged_tools") or []
        added = provenance.get("added_tools") or []
        removed = provenance.get("removed_tools") or []
        carried = provenance.get("carried_tools") or []
        changed = rejudged + added
        total = len(changed) + len(carried)
        detail = []
        if rejudged:
            detail.append(f"Re-judged: {', '.join(rejudged)}.")
        if added:
            detail.append(f"Added: {', '.join(added)}.")
        if removed:
            detail.append(f"Removed: {', '.join(removed)}.")
        return (
            f"{len(changed)} of {total} tools changed{since_clause}. "
            + " ".join(detail)
            + f" The other {len(carried)} tools are carried forward unchanged."
            + net
        )

    if reason == cf.NO_BASELINE:
        return (
            "No previous review to compare against; this is the first recorded "
            "run for this endpoint."
        )

    caveat = (
        " All tools were re-judged, so count changes below may reflect the "
        "judge rather than your tools."
    )
    changes = provenance.get("component_changes") or {}
    if reason == cf.MODEL_CHANGED:
        return (
            f"Judge model changed{since_clause}"
            f"{_transition(changes.get('judge_model'))}.{caveat}{net}"
        )
    if reason == cf.STYLE_GUIDE_CHANGED:
        return (
            f"Style guide changed{since_clause}"
            f"{_transition(changes.get('style_guide_path'))}.{caveat}{net}"
        )
    if reason == cf.WAIVERS_CHANGED:
        return (
            f"Waived rules changed{since_clause}.{caveat}{net}"
        )
    if reason == cf.PROMPT_CHANGED:
        return f"The review prompt changed{since_clause}.{caveat}{net}"
    if reason == cf.BASELINE_EXPIRED:
        return (
            f"The previous review{since_clause} has aged out and was "
            f"re-derived from scratch.{caveat}{net}"
        )
    if reason == cf.FORCED_REFRESH:
        return f"A full re-review was requested for this run.{caveat}{net}"
    if reason == cf.ENDPOINT_IDENTITY_CHANGED:
        return (
            "This endpoint's identity changed, so its previous review could "
            "not be matched to it."
        )
    if reason == cf.BASELINE_UNAVAILABLE:
        return (
            f"The previous review{since_clause} could not be read or compared, "
            f"so every tool was re-judged.{caveat}{net}"
        )
    return f"Every tool was re-judged ({reason})."


def _transition(change: dict | None) -> str:
    """Render a recorded component change as " (a → b)", else an empty string."""
    if not change or change.get("from") in (None, "") or change.get("to") in (
        None,
        "",
    ):
        return ""
    return f" ({change['from']} → {change['to']})"


def _date_of(timestamp: str) -> str:
    return str(timestamp or "").split("T")[0]


def _tool_names(tools) -> list[str]:
    return [name for name in (getattr(t, "name", "") for t in tools or []) if name]


def _judge_model_name(model_config_path: str) -> str:
    """The judge's model id, read from its model config.

    Falls back to the config path when the file cannot be read: a stable string
    is all the fingerprint needs, and an unreadable model config would already
    have failed at generator construction.
    """
    try:
        config = load_yaml_config(model_config_path) or {}
    except Exception:
        return str(model_config_path)
    return str(config.get("vertex_model") or model_config_path)


def _clean_findings_by_tool(by_tool) -> list[dict]:
    """The judge's per-tool findings, with unusable entries dropped.

    The judge groups findings itself, so this only guards the shape: an entry
    needs a tool name and a list of dict findings to be renderable. Entry and
    finding order are the judge's -- it is told to lead with "general" and to
    order findings P0 -> P1 -> P2.
    """
    if not isinstance(by_tool, list):
        return []
    cleaned = []
    for entry in by_tool:
        if not isinstance(entry, dict):
            continue
        tool = str(entry.get("tool", "")).strip()
        raw_findings = entry.get("findings")
        if not isinstance(raw_findings, list):
            continue
        findings = [f for f in raw_findings if isinstance(f, dict)]
        if tool and findings:
            cleaned.append({"tool": tool, "findings": findings})
    return cleaned


def _severity_counts(findings: list) -> dict[str, int]:
    """P0/P1/P2 as the number of findings at each severity.

    The judge reports a violation once per affected tool, and each of those
    occurrences counts: a rule broken by ten tools is ten findings.
    """
    counts = {"P0": 0, "P1": 0, "P2": 0}
    for f in findings:
        if not isinstance(f, dict):
            continue
        sev = str(f.get("severity", "")).upper()
        if sev in counts:
            counts[sev] += 1
    return counts


def _public_feedback(feedback: dict) -> dict:
    """The feedback dict as persisted to the JSON column: no readability score.

    Keeps every structured field a human or downstream tool needs (findings,
    counts, waived rules, summary) while dropping the numeric score so neither
    feedback column reports a grade.
    """
    return {k: v for k, v in feedback.items() if k != "readability_score"}


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _finish_reason_name(resp) -> str:
    """The first candidate's ``finish_reason`` as an uppercase name string.

    Robust to both the ``google.genai`` ``FinishReason`` enum (use ``.name``)
    and a plain string/None; returns ``""`` when no candidate is present.
    """
    candidates = getattr(resp, "candidates", None) or []
    if not candidates:
        return ""
    reason = getattr(candidates[0], "finish_reason", None)
    if reason is None:
        return ""
    return str(getattr(reason, "name", reason)).upper()


def _read_text(path: str) -> str:
    """Read a text file (the style guide). Raises on an unreadable path."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise ValueError(
            f"mcp_style_readability: could not read style_guide {path!r}: {e}"
        ) from e
