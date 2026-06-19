"""Evaluator for MCP compliance-check runs.

Per endpoint:
  1. Connect via McpToolsetClient, list tools.
  2. Render each tool's schema as YAML, count approximate tokens.
  3. (Phase 6) Call the judge LLM once with the YAML bundle + readability
     guide. Parse findings into per-tool slices + general findings.
  4. Emit N per-tool eval_outputs + 1 aggregate eval_output.
  5. Run scorers (`mcp_compliance_check` + `mcp_token_cost`) on each row.

The judge call lives here (not in the scorer) so the LLM is hit exactly
once per endpoint and per-row state stays minimal — see design doc §7.1.1.
"""

from __future__ import annotations

import concurrent.futures
import datetime
import json
import logging
import re
import threading
from typing import Any

from dataset.evalmcpendpointinput import EvalMcpEndpointRequest
from evaluator.mcp_compliance_html import render_endpoint_summary, render_tool_slice
from evaluator.mcp_compliance_prompt import build_prompt
from generators import models as model_loader
from generators.models.mcp_toolset import McpProbeError, McpToolsetClient, schema_to_yaml
from mp import mprunner
from scorers import score as scorer
from work.work import Work


AGGREGATE_TOOL_NAME = "__aggregate__"


class ComplianceCheckEvaluator:
    def __init__(self, config: dict):
        self.config = config
        self.probe_cfg = config.get("probe") or {}
        self.list_tools_timeout_s = self.probe_cfg.get("list_tools_timeout_s", 30)
        self.fail_on_unreachable = self.probe_cfg.get("fail_on_unreachable", False)

        self.judge_cfg = config.get("judge") or {}
        self.judge_model = None
        self.readability_guide_md: str | None = None
        self._global_models = {
            "lock": threading.Lock(),
            "registered_models": {},
        }
        if self.judge_cfg:
            model_config_path = self.judge_cfg.get("model_config")
            guide_path = self.judge_cfg.get("readability_guide")
            if model_config_path and guide_path:
                self.judge_model = model_loader.get_generator(
                    self._global_models, model_config_path
                )
                with open(guide_path, "r") as f:
                    self.readability_guide_md = f.read()
                logging.info(
                    "Judge LLM loaded from %s; guide loaded from %s",
                    model_config_path,
                    guide_path,
                )
            else:
                logging.warning(
                    "judge: block missing 'model_config' or 'readability_guide'; "
                    "compliance findings will be empty."
                )

        runner_config = self.config.get("runners", {}) or {}
        self.endpoint_runners = runner_config.get("endpoint_runners", 4)
        self.runner = mprunner.MPRunner(self.endpoint_runners)

    def evaluate(
        self,
        dataset: list[EvalMcpEndpointRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        eval_outputs: list[dict] = []
        scoring_results: list[dict] = []

        self.runner.futures.clear()

        for item in dataset:
            payload = json.loads(item.payload)
            endpoints = payload.get("endpoints") or []
            if not endpoints:
                logging.warning("Dataset item %s has no endpoints; skipping", item.id)
                continue
            for endpoint_cfg in endpoints:
                work = _EndpointWork(
                    evaluator=self,
                    endpoint_cfg=endpoint_cfg,
                    job_id=job_id,
                )
                self.runner.execute_work(work)

        for future in concurrent.futures.as_completed(self.runner.futures):
            result = future.result()
            if result is None:
                continue
            eval_outputs.extend(result["eval_outputs"])
            scoring_results.extend(result["scoring_results"])

        return eval_outputs, scoring_results

    # ----------------- per-endpoint processing -----------------

    def process_endpoint(self, endpoint_cfg: dict, job_id: str) -> dict:
        endpoint_id = endpoint_cfg.get("id") or "<unnamed>"
        logging.info("Probing MCP endpoint: %s", endpoint_id)

        try:
            tools = McpToolsetClient(endpoint_cfg).list_tools_sync(
                timeout_s=self.list_tools_timeout_s
            )
        except McpProbeError as e:
            logging.error("Endpoint %s failed to probe: %s", endpoint_id, e)
            if self.fail_on_unreachable:
                raise
            return self._error_row(endpoint_id, str(e), job_id)

        # Render YAML + token counts per tool.
        tool_yaml: dict[str, str] = {}
        tool_tokens: dict[str, int] = {}
        for tool in tools:
            name = tool.get("name") or "<unnamed>"
            yaml_str = schema_to_yaml(tool)
            tool_yaml[name] = yaml_str
            tool_tokens[name] = len(yaml_str) // 4

        # One judge LLM call per endpoint (or empty findings if no judge).
        if self.judge_model and self.readability_guide_md:
            judgment = self._run_judge(endpoint_id, tool_yaml)
        else:
            judgment = _empty_judgment(tool_yaml.keys())

        eval_outputs = self._build_eval_outputs(
            endpoint_id=endpoint_id,
            endpoint_cfg=endpoint_cfg,
            tools=tools,
            tool_yaml=tool_yaml,
            tool_tokens=tool_tokens,
            judgment=judgment,
            job_id=job_id,
        )

        scoring_results = self._run_scorers(eval_outputs)
        return {"eval_outputs": eval_outputs, "scoring_results": scoring_results}

    def _run_judge(self, endpoint_id: str, tool_yaml: dict[str, str]) -> dict:
        prompt = build_prompt(self.readability_guide_md or "", tool_yaml)
        try:
            raw = self.judge_model.generate(prompt)
        except Exception as e:
            logging.exception("Judge LLM call failed for %s: %s", endpoint_id, e)
            return _empty_judgment(tool_yaml.keys())

        parsed = _extract_judgment_json(raw)
        if parsed is None:
            # Persist the raw output so we can debug parser failures.
            dump_path = f"/tmp/mcp_judge_raw_{endpoint_id.replace('/', '_')}.txt"
            try:
                with open(dump_path, "w") as f:
                    f.write(raw or "")
                logging.error(
                    "Judge for %s returned unparseable JSON; raw output dumped to %s",
                    endpoint_id, dump_path,
                )
            except Exception:
                logging.error(
                    "Judge for %s returned unparseable JSON; first 500 chars: %r",
                    endpoint_id, (raw or "")[:500],
                )
            return _empty_judgment(tool_yaml.keys())
        return _normalize_judgment(parsed, tool_yaml.keys())

    def _build_eval_outputs(
        self,
        endpoint_id: str,
        endpoint_cfg: dict,
        tools: list[dict],
        tool_yaml: dict[str, str],
        tool_tokens: dict[str, int],
        judgment: dict,
        job_id: str,
    ) -> list[dict]:
        outputs: list[dict] = []
        per_tool_findings = judgment.get("per_tool", {})

        # Per-tool rows.
        for tool in tools:
            name = tool["name"]
            findings = per_tool_findings.get(name, {"P0": [], "P1": [], "P2": []})
            outputs.append(
                {
                    "eval_id": f"{endpoint_id}::{name}",
                    "endpoint_id": endpoint_id,
                    "tool_name": name,
                    "is_aggregate": False,
                    "raw_schema": tool.get("inputSchema"),
                    "formatted_yaml": tool_yaml[name],
                    "approx_tokens": tool_tokens[name],
                    "findings": findings,
                    "severity_counts": _count_severities(findings),
                    "html_slice": render_tool_slice(name, findings),
                    "probe_error": None,
                    "job_id": job_id,
                }
            )

        # Aggregate row.
        general_findings = judgment.get("general", {"P0": [], "P1": [], "P2": []})
        total_counts = _count_severities(general_findings)
        for findings in per_tool_findings.values():
            for sev, n in _count_severities(findings).items():
                total_counts[sev] += n

        tokens_list = list(tool_tokens.values())
        total_tokens = sum(tokens_list)
        avg_tokens = (total_tokens // len(tokens_list)) if tokens_list else 0

        outputs.append(
            {
                "eval_id": f"{endpoint_id}::{AGGREGATE_TOOL_NAME}",
                "endpoint_id": endpoint_id,
                "tool_name": AGGREGATE_TOOL_NAME,
                "is_aggregate": True,
                "tool_count": len(tools),
                "total_approx_tokens": total_tokens,
                "avg_approx_tokens": avg_tokens,
                "general_findings": general_findings,
                "severity_counts": total_counts,
                "html_summary": render_endpoint_summary(
                    endpoint_id=endpoint_id,
                    tool_count=len(tools),
                    avg_tokens=avg_tokens,
                    total_tokens=total_tokens,
                    severity_counts=total_counts,
                    general_findings=general_findings,
                    per_tool_findings=per_tool_findings,
                ),
                "probe_error": None,
                "job_id": job_id,
            }
        )
        return outputs

    def _error_row(self, endpoint_id: str, err: str, job_id: str) -> dict:
        row = {
            "eval_id": f"{endpoint_id}::{AGGREGATE_TOOL_NAME}",
            "endpoint_id": endpoint_id,
            "tool_name": AGGREGATE_TOOL_NAME,
            "is_aggregate": True,
            "tool_count": 0,
            "total_approx_tokens": 0,
            "avg_approx_tokens": 0,
            "general_findings": {"P0": [], "P1": [], "P2": []},
            "severity_counts": {"P0": 0, "P1": 0, "P2": 0},
            "html_summary": "",
            "probe_error": err,
            "job_id": job_id,
        }
        # Single aggregate row, no per-tool rows; scorers still get to run
        # over it (they'll noop / emit a low score on the error).
        return {
            "eval_outputs": [row],
            "scoring_results": self._run_scorers([row]),
        }

    def _run_scorers(self, eval_outputs: list[dict]) -> list[dict]:
        results: list[dict] = []
        for eo in eval_outputs:
            scoring_item = {
                "id": eo["eval_id"],
                "nl_prompt": f"MCP endpoint {eo['endpoint_id']} tool {eo['tool_name']}",
                "golden_sql": "",
                "query_type": "",
                "golden_result": [],
                "golden_eval_results": "",
                "golden_error": "",
                "generated_sql": "skipped",
                # The scorers read structured fields out of this dict.
                "generated_result": eo,
                "eval_results": eo,
                "generated_error": eo.get("probe_error"),
                "dialects": [],
                "database": eo.get("endpoint_id", "unknown"),
                "job_id": eo["job_id"],
            }
            scorer.compare(
                eval_output_item=scoring_item,
                experiment_config=self.config,
                scoring_results=results,
                global_models=_thread_local_global_models(),
            )
        return results


# ----------------- helpers -----------------

_TLS = threading.local()


def _thread_local_global_models() -> dict:
    """Per-thread global_models dict for scorer.compare's signature.

    Our new scorers don't actually use it (they're thin extractors), but
    compare() requires it. Keep one per thread to avoid cross-thread locking.
    """
    if not hasattr(_TLS, "gm"):
        _TLS.gm = {
            "lock": threading.Lock(),
            "registered_models": {},
            "semaphores": {},
        }
    return _TLS.gm


def _count_severities(findings: dict) -> dict[str, int]:
    return {sev: len(findings.get(sev) or []) for sev in ("P0", "P1", "P2")}


def _empty_judgment(tool_names) -> dict:
    return {
        "general": {"P0": [], "P1": [], "P2": []},
        "per_tool": {name: {"P0": [], "P1": [], "P2": []} for name in tool_names},
    }


_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*([\s\S]*?)\s*```", re.IGNORECASE)


_LEADING_LANG_HINT_RE = re.compile(r"^\s*(?:```)?\s*json\s*\n", re.IGNORECASE)
_TRAILING_FENCE_RE = re.compile(r"\n\s*```\s*$")


def _extract_judgment_json(raw: str) -> dict | None:
    """Pull a JSON object out of the judge's text response.

    Tolerant of:
    - leading/trailing prose
    - markdown code fences (```json ... ```)
    - bare ``json`` language hint without fences (a Gemini quirk)
    - trailing prose after the JSON object
    - a raw JSON object

    Returns None if nothing parseable is found.
    """
    if not raw:
        return None

    candidates: list[str] = []

    # Strip a bare ``json`` line (or fence + json) prefix + trailing fence.
    stripped = _LEADING_LANG_HINT_RE.sub("", raw)
    stripped = _TRAILING_FENCE_RE.sub("", stripped).strip()
    if stripped:
        candidates.append(stripped)

    # Also try anything inside explicit fenced blocks.
    candidates.extend(m.group(1) for m in _JSON_FENCE_RE.finditer(raw))

    # Last resort: brute slice from first '{' to last '}'.
    first = raw.find("{")
    last = raw.rfind("}")
    if first != -1 and last > first:
        candidates.append(raw[first : last + 1])

    for c in candidates:
        try:
            parsed = json.loads(c)
            if isinstance(parsed, dict):
                return parsed
        except json.JSONDecodeError:
            continue
    return None


def _normalize_judgment(judgment: dict, tool_names) -> dict:
    """Ensure the judgment has the expected nested shape regardless of
    minor schema variation from the LLM (missing severity keys, missing
    `general`, etc.)."""

    def _bucket(d: Any) -> dict:
        if not isinstance(d, dict):
            return {"P0": [], "P1": [], "P2": []}
        return {sev: list(d.get(sev) or []) for sev in ("P0", "P1", "P2")}

    general = _bucket(judgment.get("general"))
    per_tool_in = judgment.get("per_tool") or {}
    per_tool: dict[str, dict] = {}
    for name in tool_names:
        per_tool[name] = _bucket(per_tool_in.get(name))
    return {"general": general, "per_tool": per_tool}


class _EndpointWork(Work):
    """One Work unit = one endpoint to probe + score."""

    def __init__(self, evaluator: "ComplianceCheckEvaluator", endpoint_cfg: dict, job_id: str):
        self.evaluator = evaluator
        self.endpoint_cfg = endpoint_cfg
        self.job_id = job_id

    def run(self, work_config: Any = None):
        try:
            return self.evaluator.process_endpoint(self.endpoint_cfg, self.job_id)
        except Exception as e:
            logging.exception("Endpoint %s crashed: %s", self.endpoint_cfg.get("id"), e)
            return None
