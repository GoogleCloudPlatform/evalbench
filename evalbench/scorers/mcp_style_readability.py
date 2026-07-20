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
from scorers.mcp_readability_scoring import EndpointContext, ScoreContribution


# Shared JSON output contract appended to both prompts (escaped for str.format).
_OUTPUT_SCHEMA = """### OUTPUT
Return ONLY a JSON object (no markdown, no prose) with exactly this shape:
{{
  "readability_score": <integer 0-100, higher is better>,
  "p0_issues": <integer>,
  "p1_issues": <integer>,
  "p2_issues": <integer>,
  "findings": [
    {{"severity": "P0|P1|P2", "rule_id": "<string>", "tool": "<tool name or ''>",
      "title": "<short one-line summary>", "message": "<what is wrong>",
      "suggestion": "<how to fix>"}}
  ],
  "waived": [
    {{"rule_id": "<string>", "reason": "<reason>", "would_have_violated": <true|false>}}
  ],
  "summary": "<one-paragraph overall assessment>"
}}
The counts p0_issues/p1_issues/p2_issues MUST equal the number of findings of
each severity."""


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

Avoid duplication (global vs per-tool):
- If an issue is global or repeats across many tools (e.g. a convention violated
  everywhere), report it ONCE with "tool" set to "" (empty string). Do NOT emit
  the same global issue once per tool.
- Report tool-specific issues with "tool" set to that tool's name.

### STYLE GUIDE
{style_guide}

### PRODUCT
{product_name}

### TOOLS (man page)
{tools_markup}

### EXCEPTIONS (waived rules — DO NOT count these as issues)
The following rules have been explicitly waived for this endpoint. Do not include
them in p0_issues/p1_issues/p2_issues or in "findings". Instead list them under
"waived" with their reason. If a waived rule would otherwise have been violated,
note that in the waived entry.
{exceptions}

"""
    + _OUTPUT_SCHEMA
)


class McpStyleReadabilityScorer:
    """Scores a tools spec against the MCP style guide using an LLM."""

    # Result-row columns this scorer contributes.
    COLUMNS = [
        "mcp_readability_p0_issues",
        "mcp_readability_p1_issues",
        "mcp_readability_p2_issues",
        "mcp_readability_score",
        "mcp_readability_llm_feedback_json",
        "mcp_readability_llm_feedback_html",
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
        self.model = get_generator(global_models, self.model_config)

    def run(self, context: EndpointContext) -> ScoreContribution:
        """Evaluate one endpoint: judge the man page, pass iff no P0 findings."""
        feedback = self.evaluate(
            tools_markup=context.man_page,
            style_guide=self.style_guide,
            product_name=context.product_name,
            exceptions=context.exceptions,
        )
        p0 = int(feedback.get("p0_issues", 0))
        return ScoreContribution(
            row_fields={
                "mcp_readability_p0_issues": p0,
                "mcp_readability_p1_issues": int(feedback.get("p1_issues", 0)),
                "mcp_readability_p2_issues": int(feedback.get("p2_issues", 0)),
                "mcp_readability_score": int(feedback.get("readability_score", 0)),
                # Both feedback columns omit the readability score on purpose;
                # only the numeric metric column above carries it.
                "mcp_readability_llm_feedback_json": json.dumps(
                    _public_feedback(feedback)
                ),
                "mcp_readability_llm_feedback_html": self.to_html(
                    feedback, context.product_name
                ),
            },
            score=100 if p0 == 0 else 0,
            logs=(
                f"p0_issues={p0}, "
                f"readability_score={feedback.get('readability_score', 0)}"
            ),
        )

    def evaluate(
        self,
        tools_markup: str,
        style_guide: str,
        product_name: str,
        exceptions: list[dict] | None = None,
    ) -> dict:
        """Run the LLM readability check and return a normalized feedback dict."""
        prompt = PROMPT_TEMPLATE.format(
            style_guide=style_guide or "(no style guide provided)",
            product_name=product_name or "(unknown)",
            tools_markup=tools_markup or "(no tools)",
            exceptions=json.dumps(exceptions or [], indent=2),
        )
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
                )
                resp = caller(contents=prompt, config=config)
                text = getattr(resp, "text", None)
                if text:
                    return text
            except Exception as e:
                logging.warning(
                    "mcp_style_readability: JSON-mode generation failed (%s); "
                    "falling back to plain generate().",
                    e,
                )
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
        findings = data.get("findings") or []
        if not isinstance(findings, list):
            findings = []

        # Counts derived from findings are authoritative (a single pass). Only
        # fall back to the model's self-reported integers when there are no
        # findings at all -- otherwise a legitimately-zero severity would be
        # overwritten by a mismatched self-reported count.
        if findings:
            counts = {"P0": 0, "P1": 0, "P2": 0}
            for f in findings:
                if isinstance(f, dict):
                    sev = str(f.get("severity", "")).upper()
                    if sev in counts:
                        counts[sev] += 1
            p0, p1, p2 = counts["P0"], counts["P1"], counts["P2"]
        else:
            p0 = _safe_int(data.get("p0_issues"))
            p1 = _safe_int(data.get("p1_issues"))
            p2 = _safe_int(data.get("p2_issues"))

        return {
            "readability_score": _safe_int(data.get("readability_score")),
            "p0_issues": p0,
            "p1_issues": p1,
            "p2_issues": p2,
            "findings": findings,
            "waived": data.get("waived") or [],
            "summary": data.get("summary", ""),
        }

    @staticmethod
    def to_html(feedback: dict, product_name: str = "") -> str:
        """Render feedback as a human-readable HTML fragment.

        Leads with the overall summary, groups findings by severity, and ends
        with the allowed exceptions (waived rules) and their reasons. It
        deliberately omits any numeric readability score -- the intent is review
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

        summary = str(feedback.get("summary", "")).strip()
        if summary:
            parts.append(f"<p><b>Summary:</b> {esc(summary)}</p>")

        # Group findings by severity so each section can be rendered in order.
        by_sev = {"P0": [], "P1": [], "P2": []}
        for f in feedback.get("findings") or []:
            if isinstance(f, dict):
                sev = str(f.get("severity", "")).upper()
                if sev in by_sev:
                    by_sev[sev].append(f)

        for sev, heading in _SEVERITY_SECTIONS:
            items = by_sev[sev]
            parts.append(f"<h4>{heading} — {len(items)}</h4>")
            if not items:
                parts.append("<p><i>None</i></p>")
                continue
            parts.append("<ul>")
            for f in items:
                rule = esc(str(f.get("rule_id", "")).strip() or "(rule)")
                tool = esc(str(f.get("tool", "")).strip() or "all tools")
                li = [f"<b>[{rule}] {tool}</b>"]
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


# Severity display order + section heading for the HTML feedback report.
_SEVERITY_SECTIONS = [
    ("P0", "🚫 Blockers (P0)"),
    ("P1", "⚠️ Recommended (P1)"),
    ("P2", "💡 Suggestions (P2)"),
]


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


def _read_text(path: str) -> str:
    """Read a text file (the style guide). Raises on an unreadable path."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            return f.read()
    except OSError as e:
        raise ValueError(
            f"mcp_style_readability: could not read style_guide {path!r}: {e}"
        ) from e
