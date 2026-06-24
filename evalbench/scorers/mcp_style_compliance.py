"""LLM-backed scorer that evaluates an MCP tool manifest against a style guide.

Follows the same shape as :class:`scorers.llmrater.LLMRater`: constructed with
``(config, global_models)`` and holds an LLM obtained via
``generators.models.get_generator``. The orchestrator calls :meth:`evaluate`
per endpoint with the tool man-page markup.

The model reviews the tools from an LLM-agent-consumption perspective and returns
a strict JSON object describing P0/P1/P2 findings, an overall compliance score,
and any rules waived via the exceptions file. We parse and normalize that JSON
defensively so a slightly malformed response degrades to an ERROR row rather than
crashing the run.

When a previous run's feedback is available, a dedicated second LLM pass
reconciles the fresh review with it (preserving historical severity, dropping
fixed issues, adding new ones) so results stay consistent run-to-run.
"""

import html
import json
import logging
import re

from generators.models import get_generator


# Shared JSON output contract appended to both prompts (escaped for str.format).
_OUTPUT_SCHEMA = """### OUTPUT
Return ONLY a JSON object (no markdown, no prose) with exactly this shape:
{{
  "compliance_score": <integer 0-100, higher is better>,
  "p0_issues": <integer>,
  "p1_issues": <integer>,
  "p2_issues": <integer>,
  "findings": [
    {{"severity": "P0|P1|P2", "rule_id": "<string>", "tool": "<tool name or ''>",
      "message": "<what is wrong>", "suggestion": "<how to fix>"}}
  ],
  "waived": [
    {{"rule_id": "<string>", "reason": "<reason>", "would_have_violated": <true|false>}}
  ],
  "summary": "<one-paragraph overall assessment>"
}}
The counts p0_issues/p1_issues/p2_issues MUST equal the number of findings of
each severity."""


PROMPT_TEMPLATE = (
    """You are an expert on the Data Cloud MCP / OneMCP standard and a pragmatic
API Developer Experience reviewer. Evaluate the MCP server's tool definitions
(shown below as a man page) against the STYLE GUIDE and report every violation.

Evaluate from the perspective of an LLM agent that must call these tools:
- Will the model understand the terminology and the tool / parameter names?
- Are the parameters too complex, too numerous, or under-described?
- Is the agent forced to act like a computer -- e.g. formatting complex strings,
  generating UUIDs, or calculating timestamps -- instead of expressing intent?
Adopt a consultative, pragmatic tone (e.g. "Consider...", "Evaluate whether...").
Do not be overly pedantic about minor wording when larger architectural blockers
exist.

Severity levels:
- P0: critical violation (must fix; blocks compliance).
- P1: major violation (should fix).
- P2: minor / stylistic violation (nice to fix).

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


# Second pass: reconcile the just-produced review with the previous run's review
# so findings stay consistent over time (preserve historical severity, drop
# fixed issues, add genuinely new ones). Both reviews share the schema above.
CONSISTENCY_PROMPT_TEMPLATE = (
    """You are maintaining run-to-run consistency for the Data Cloud MCP
style-guide review of a single endpoint. You are given the STYLE GUIDE, the
current tool manifest (man page), the review just produced for it ("LATEST
REVIEW"), and the review from the previous run ("PREVIOUS REVIEW"). Both reviews
use the JSON schema described under OUTPUT.

Produce a single reconciled review:
1. For each finding in PREVIOUS REVIEW, check it against the current manifest. If
   the violation STILL EXISTS, keep it and PRESERVE its original severity and
   wording. Drop findings that are now fixed.
2. Add genuinely new violations from LATEST REVIEW that were missed previously.
3. If a finding appears in both, prefer the PREVIOUS REVIEW's wording and
   severity, unless LATEST REVIEW is significantly more precise about the same
   issue.
4. Do not downgrade or upgrade a still-valid finding's severity unless the STYLE
   GUIDE's annotated priority for that rule has actually changed.
5. Carry forward the "waived" entries that still apply.

### STYLE GUIDE
{style_guide}

### TOOLS (man page)
{tools_markup}

### LATEST REVIEW (JSON)
{latest_review}

### PREVIOUS REVIEW (JSON)
{previous_review}

"""
    + _OUTPUT_SCHEMA
)


class McpStyleComplianceScorer:
    """Scores a tools spec against the MCP style guide using an LLM."""

    def __init__(self, config: dict, global_models):
        self.name = "mcp_style_compliance"
        config = config or {}
        self.model_config = config.get("model_config") or ""
        if not self.model_config:
            raise ValueError(
                "model_config is required for the mcp_style_compliance scorer"
            )
        self.model = get_generator(global_models, self.model_config)

    def evaluate(
        self,
        tools_markup: str,
        style_guide: str,
        product_name: str,
        exceptions: list[dict] | None = None,
        previous_feedback: str | None = None,
    ) -> dict:
        """Run the LLM compliance check and return a normalized feedback dict.

        When ``previous_feedback`` (a prior run's ``llm_feedback_json``) is
        supplied, a dedicated second pass reconciles the fresh review with it so
        findings stay consistent run-to-run.
        """
        prompt = PROMPT_TEMPLATE.format(
            style_guide=style_guide or "(no style guide provided)",
            product_name=product_name or "(unknown)",
            tools_markup=tools_markup or "(no tools)",
            exceptions=json.dumps(exceptions or [], indent=2),
        )
        raw = self._generate(prompt)
        feedback = self._parse(raw)

        prev = (previous_feedback or "").strip()
        if prev:
            feedback = self._consistency_check(
                tools_markup=tools_markup,
                style_guide=style_guide,
                latest=feedback,
                previous=prev,
            )
        return feedback

    def _consistency_check(
        self, tools_markup: str, style_guide: str, latest: dict, previous: str
    ) -> dict:
        """Reconcile ``latest`` with the ``previous`` run's review (JSON string).

        Returns the merged feedback, or ``latest`` unchanged if the pass fails.
        """
        try:
            prompt = CONSISTENCY_PROMPT_TEMPLATE.format(
                style_guide=style_guide or "(no style guide provided)",
                tools_markup=tools_markup or "(no tools)",
                latest_review=json.dumps(latest),
                previous_review=previous,
            )
            return self._parse(self._generate(prompt))
        except Exception as e:
            logging.warning(
                "mcp_style_compliance: consistency pass failed (%s); "
                "using latest review.",
                e,
            )
            return latest

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
                    "mcp_style_compliance: JSON-mode generation failed (%s); "
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
        # Strip ```json ... ``` or ``` ... ``` fences.
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```$", text, re.DOTALL)
        if fence:
            text = fence.group(1).strip()
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            # Fallback: grab the outermost {...} span.
            start = text.find("{")
            end = text.rfind("}")
            if start != -1 and end != -1 and end > start:
                return json.loads(text[start : end + 1])
            raise

    def _parse(self, raw: str) -> dict:
        """Normalize the model output into a stable feedback dict."""
        data = self._extract_json(raw)
        findings = data.get("findings") or []
        if not isinstance(findings, list):
            findings = []

        def _count(sev):
            return sum(
                1
                for f in findings
                if isinstance(f, dict)
                and str(f.get("severity", "")).upper() == sev
            )

        # Prefer counts derived from findings (authoritative); fall back to the
        # model-reported integers if findings are absent.
        p0 = _count("P0") or _safe_int(data.get("p0_issues"))
        p1 = _count("P1") or _safe_int(data.get("p1_issues"))
        p2 = _count("P2") or _safe_int(data.get("p2_issues"))

        return {
            "compliance_score": _safe_int(data.get("compliance_score")),
            "p0_issues": p0,
            "p1_issues": p1,
            "p2_issues": p2,
            "findings": findings,
            "waived": data.get("waived") or [],
            "summary": data.get("summary", ""),
        }

    @staticmethod
    def to_html(feedback: dict) -> str:
        """Render feedback as a severity-grouped HTML fragment."""
        if not feedback:
            return ""
        score = feedback.get("compliance_score", 0)
        summary = html.escape(str(feedback.get("summary", "")))
        parts = [
            "<div class='mcp-compliance'>",
            f"<h3>Compliance score: {score}</h3>",
            f"<p>{summary}</p>",
            "<p>P0: {p0} &nbsp; P1: {p1} &nbsp; P2: {p2}</p>".format(
                p0=feedback.get("p0_issues", 0),
                p1=feedback.get("p1_issues", 0),
                p2=feedback.get("p2_issues", 0),
            ),
        ]
        findings = feedback.get("findings") or []
        if findings:
            parts.append(
                "<table border='1' cellpadding='4' cellspacing='0'>"
                "<tr><th>Severity</th><th>Rule</th><th>Tool</th>"
                "<th>Issue</th><th>Suggestion</th></tr>"
            )
            for f in findings:
                if not isinstance(f, dict):
                    continue
                parts.append(
                    "<tr><td>{sev}</td><td>{rule}</td><td>{tool}</td>"
                    "<td>{msg}</td><td>{sug}</td></tr>".format(
                        sev=html.escape(str(f.get("severity", ""))),
                        rule=html.escape(str(f.get("rule_id", ""))),
                        tool=html.escape(str(f.get("tool", ""))),
                        msg=html.escape(str(f.get("message", ""))),
                        sug=html.escape(str(f.get("suggestion", ""))),
                    )
                )
            parts.append("</table>")
        waived = feedback.get("waived") or []
        if waived:
            parts.append("<h4>Waived rules</h4><ul>")
            for w in waived:
                if not isinstance(w, dict):
                    continue
                parts.append(
                    "<li>{rule}: {reason}</li>".format(
                        rule=html.escape(str(w.get("rule_id", ""))),
                        reason=html.escape(str(w.get("reason", ""))),
                    )
                )
            parts.append("</ul>")
        parts.append("</div>")
        return "".join(parts)


def _safe_int(value) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0
