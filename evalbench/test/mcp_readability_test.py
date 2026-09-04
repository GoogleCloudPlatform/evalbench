import json
import os
import sys
import tempfile
from unittest.mock import patch

import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Repo root (two levels up from evalbench/evalbench/test/) so dataset paths
# resolve regardless of the pytest working directory.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def _ds(rel):
    return os.path.join(_REPO_ROOT, rel)


from evaluator.mcp_readability import exceptions as exc_mod
from evaluator.mcp_readability.orchestrator import (
    ALLOWED_ENDPOINT_TYPES,
    BASE_COLUMNS,
    McpReadabilityOrchestrator,
    _validate_endpoint_type,
)
from mcp import types as mcp_types
from generators.models.mcp_tools import McpToolsGenerator, McpToolsError
from generators.models.mcp_tool_formatter import format_tools_to_man_page
from scorers.mcp_readability_scoring import EndpointContext
from scorers.mcp_style_readability import McpStyleReadabilityScorer
from scorers.mcp_tool_metrics import McpToolMetricsScorer


# --------------------------------------------------------------------------
# endpoint_type validation (fail-fast, no enum)
# --------------------------------------------------------------------------
def test_endpoint_type_validation():
    # Case-insensitive, returns the canonical upper-case name.
    assert _validate_endpoint_type("PROD") == "PROD"
    assert _validate_endpoint_type("dev") == "DEV"
    assert _validate_endpoint_type(" AutoPush ") == "AUTOPUSH"
    assert set(ALLOWED_ENDPOINT_TYPES) == {"PROD", "AUTOPUSH", "STAGING", "DEV"}
    # Unknown / missing values fail fast.
    with pytest.raises(ValueError):
        _validate_endpoint_type("bogus")
    with pytest.raises(ValueError):
        _validate_endpoint_type(None)


# --------------------------------------------------------------------------
# Exceptions matching (by product_name / endpoint_type)
# --------------------------------------------------------------------------
def test_exceptions_matching():
    all_exc = [
        {"product_name": "Cloud SQL", "rule_id": "R1", "reason": "x"},
        {"endpoint_type": "AUTOPUSH", "rule_id": "R2", "reason": "y"},
        {"rule_id": "R3", "reason": "global"},  # match-all
        {"reason": "no rule id, ignored"},
    ]
    ep = {"product_name": "Cloud SQL", "endpoint_type": "PROD"}
    matched = {e["rule_id"] for e in exc_mod.applicable_exceptions(ep, all_exc)}
    assert matched == {"R1", "R3"}

    ep2 = {"product_name": "Other", "endpoint_type": "AUTOPUSH"}
    matched2 = {e["rule_id"] for e in exc_mod.applicable_exceptions(ep2, all_exc)}
    assert matched2 == {"R2", "R3"}


# --------------------------------------------------------------------------
# Deterministic metrics scorer
# --------------------------------------------------------------------------
def test_metrics_scorer():
    scorer = McpToolMetricsScorer()
    tools = [
        mcp_types.Tool(name="a", description="d", inputSchema={"type": "object"}),
        mcp_types.Tool(name="b", description="d", inputSchema={"type": "object"}),
    ]
    m = scorer.score(tools, token_budget=0)
    assert m["total_tools"] == 2
    assert m["estimated_tokens"] > 0
    assert m["token_budget_used_percent"] is None  # no positive budget
    m2 = scorer.score(tools, token_budget=1000)
    assert m2["token_budget_used_percent"] is not None


def test_metrics_scorer_run_pass_and_fail():
    """run(context) contributes metric columns; pass iff within token budget."""
    tools = [
        mcp_types.Tool(name="a", description="d", inputSchema={"type": "object"}),
        mcp_types.Tool(name="b", description="d", inputSchema={"type": "object"}),
    ]
    ctx = EndpointContext(
        product_name="p", endpoint={}, tools=tools, man_page="", exceptions=[]
    )
    within = McpToolMetricsScorer({"token_budget": 100000}).run(ctx)
    assert within.row_fields["mcp_readability_total_tools"] == 2
    assert within.score == 100  # comfortably within budget

    over = McpToolMetricsScorer({"token_budget": 1}).run(ctx)
    assert over.score == 0  # exceeds a 1-token budget

    # No budget configured -> nothing to exceed -> pass.
    assert McpToolMetricsScorer({}).run(ctx).score == 100

    # An endpoint may override the configured budget.
    ctx_override = EndpointContext(
        product_name="p", endpoint={"token_budget": 1}, tools=tools,
        man_page="", exceptions=[],
    )
    assert McpToolMetricsScorer({"token_budget": 100000}).run(ctx_override).score == 0


# --------------------------------------------------------------------------
# URL sanitization
# --------------------------------------------------------------------------
def test_sanitize_url():
    s = McpToolsGenerator.sanitize_url
    assert s("example.com") == "https://example.com/mcp"
    assert s("https://x.com/mcp") == "https://x.com/mcp"
    assert s("https://x.com/mcp/") == "https://x.com/mcp"
    assert s("localhost:8000") == "http://localhost:8000/mcp"
    assert s("https://x.com") == "https://x.com/mcp"


# --------------------------------------------------------------------------
# Formatter (man-page markup -> string)
# --------------------------------------------------------------------------
def test_formatter_renders_enum_default():
    tool = mcp_types.Tool(
        name="t",
        description="d",
        inputSchema={
            "type": "object",
            "properties": {
                "mode": {
                    "type": "string",
                    "enum": ["a", "b"],
                    "default": "a",
                    "description": "the mode",
                }
            },
            "required": ["mode"],
        },
    )
    man_page = format_tools_to_man_page([tool])
    assert isinstance(man_page, str)
    assert "TOOL: t" in man_page
    assert "enum:" in man_page and '"a"' in man_page
    assert "default:" in man_page


# --------------------------------------------------------------------------
# Generator (file source)
# --------------------------------------------------------------------------
def test_generator_file_source():
    gen = McpToolsGenerator({})
    endpoint = {
        "tools_source": {
            "type": "file",
            "path": _ds("datasets/mcp_readability/sample_tools.json"),
        }
    }
    tools, man_page = gen.fetch_tools(endpoint)
    assert isinstance(man_page, str)
    assert all(isinstance(t, mcp_types.Tool) for t in tools)
    assert {t.name for t in tools} == {"list_datasets", "get_job_state"}
    assert "TOOL: list_datasets" in man_page


def test_generator_missing_file_raises():
    gen = McpToolsGenerator({})
    with pytest.raises(McpToolsError):
        gen.fetch_tools({"tools_source": {"type": "file", "path": "nope.json"}})


# --------------------------------------------------------------------------
# Scorer parsing / html / run (no live LLM call)
# --------------------------------------------------------------------------
def test_scorer_parse_and_html():
    raw = """```json
    {"readability_score": 75, "findings": [
       {"severity": "P0", "rule_id": "P0-X", "tool": "t", "title": "Bad name", "message": "m", "suggestion": "s"},
       {"severity": "P2", "rule_id": "P2-Y", "tool": "t", "message": "m", "suggestion": "s"}
     ], "waived": [{"rule_id": "use-enums", "reason": "legacy", "would_have_violated": true}],
     "summary": "ok"}
    ```"""
    # Build a scorer without invoking the LLM constructor path.
    scorer = McpStyleReadabilityScorer.__new__(McpStyleReadabilityScorer)
    fb = scorer._parse(raw)
    assert fb["p0_issues"] == 1
    assert fb["p2_issues"] == 1
    assert fb["readability_score"] == 75
    # title flows through the parser unchanged.
    assert fb["findings"][0]["title"] == "Bad name"
    # The persisted feedback also carries a per-tool view of those findings.
    assert fb["findings_by_tool"] == [
        {"tool": "t", "findings": fb["findings"]}
    ]

    html = McpStyleReadabilityScorer.to_html(fb, product_name="Cloud SQL")
    # Human-readable report: product heading + summary, no numeric score.
    assert "MCP Tool Readability Review — Cloud SQL" in html
    assert "readability score" not in html.lower()
    # Findings are grouped per tool, tallied by severity, and carry their
    # title/rule with a severity badge.
    assert "<h4>t — 1 P0, 1 P2</h4>" in html
    assert "P0-X" in html and "Bad name" in html
    # Allowed exceptions section surfaces the waiver, reason, and flag note.
    assert "Allowed exceptions (waived) — 1" in html
    assert "use-enums" in html and "legacy" in html
    assert "would have been flagged: yes" in html


def test_scorer_counts_are_authoritative_from_findings():
    """A severity with zero findings must stay 0 even if the model over-reports.

    Regression: the old `_count(sev) or _safe_int(...)` fell back to the model's
    self-reported integer whenever a severity's true count was 0.
    """
    raw = json.dumps(
        {
            "readability_score": 90,
            # No P0 findings, but the model wrongly claims 3.
            "p0_issues": 3,
            "findings": [
                {"severity": "P1", "rule_id": "P1-A", "tool": "t",
                 "message": "m", "suggestion": "s"},
            ],
            "summary": "ok",
        }
    )
    scorer = McpStyleReadabilityScorer.__new__(McpStyleReadabilityScorer)
    fb = scorer._parse(raw)
    assert fb["p0_issues"] == 0  # derived from findings, not the bogus 3
    assert fb["p1_issues"] == 1

    # When there are NO findings at all, fall back to the reported integers.
    raw2 = json.dumps({"readability_score": 50, "p0_issues": 2, "findings": []})
    fb2 = scorer._parse(raw2)
    assert fb2["p0_issues"] == 2


def test_scorer_single_pass():
    """evaluate() runs a single review pass and returns normalized feedback."""

    class _OneLLM:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt):
            self.calls += 1
            return json.dumps({"readability_score": 90, "findings": [], "summary": "ok"})

    scorer = McpStyleReadabilityScorer.__new__(McpStyleReadabilityScorer)
    scorer.model = _OneLLM()
    fb = scorer.evaluate(tools_markup="x", style_guide="g", product_name="p")
    assert scorer.model.calls == 1
    assert fb["readability_score"] == 90


def test_readability_scorer_run():
    """run(context) contributes P0/P1/P2 + score columns; pass iff no P0."""
    scorer = McpStyleReadabilityScorer.__new__(McpStyleReadabilityScorer)
    scorer.name = "mcp_style_readability"
    scorer.style_guide = "guide"
    scorer.model = _FakeLLM()  # one P1 finding, no P0
    ctx = EndpointContext(
        product_name="p", endpoint={}, tools=[], man_page="mp", exceptions=[]
    )
    contrib = scorer.run(ctx)
    assert contrib.row_fields["mcp_readability_p1_issues"] == 1
    assert contrib.row_fields["mcp_readability_score"] == 80
    assert contrib.score == 100  # no P0 -> pass


def test_readability_scorer_requires_style_guide():
    """The judge owns its config: model_config and style_guide are required."""
    with pytest.raises(ValueError):
        McpStyleReadabilityScorer({"model_config": "x"}, _global_models())
    with pytest.raises(ValueError):
        McpStyleReadabilityScorer({"style_guide": "y"}, _global_models())


# --------------------------------------------------------------------------
# End-to-end orchestrator with stubbed LLM
# --------------------------------------------------------------------------
class _FakeLLM:
    def generate(self, prompt):
        return json.dumps(
            {
                "readability_score": 80,
                "findings": [
                    {"severity": "P1", "rule_id": "P1-A", "tool": "list_datasets",
                     "message": "m", "suggestion": "s"},
                ],
                "waived": [{"rule_id": "use-enums", "reason": "r"}],
                "summary": "fine",
            }
        )


def _global_models():
    import threading

    return {"lock": threading.Lock(), "registered_models": {}}


def _write_endpoints(path, product_name, source):
    import yaml

    with open(path, "w") as f:
        yaml.safe_dump(
            {
                "endpoints": [
                    {
                        "product_name": product_name,
                        "endpoint_type": "PROD",
                        "tools_source": source,
                    }
                ]
            },
            f,
        )


def _base_config(ep_path, output_dir, token_budget=25000):
    return {
        "orchestrator": "mcp_readability",
        "endpoints_config": ep_path,
        "exceptions_config": _ds("datasets/mcp_readability/exceptions.yaml"),
        "tools_generator_config": _ds(
            "datasets/mcp_readability/tools_generator.yaml"
        ),
        "endpoint_types": [],
        "scorers": {
            "mcp_tool_metrics": {"token_budget": token_budget},
            "mcp_style_readability": {
                "model_config": "unused",
                "style_guide": _ds("datasets/mcp_readability/style_guide.md"),
            },
        },
        "runners": {"endpoint_runners": 2},
        "reporting": {"csv": {"output_directory": output_dir}},
    }


def test_orchestrator_end_to_end():
    with patch(
        "scorers.mcp_style_readability.get_generator", return_value=_FakeLLM()
    ):
        from evaluator import get_orchestrator

        # Use the offline file source so the test stays deterministic and
        # independent of the production endpoint list.
        with tempfile.TemporaryDirectory() as d:
            ep_path = os.path.join(d, "endpoints.yaml")
            _write_endpoints(
                ep_path,
                "Sample",
                {
                    "type": "file",
                    "path": _ds("datasets/mcp_readability/sample_tools.json"),
                },
            )
            orch = get_orchestrator(_base_config(ep_path, d), [], {})
            orch.evaluate([])
            # process() returns the standard tuple: full readability rows in
            # results_tf and one score row per (endpoint, scorer) in scores_tf.
            job_id, _, results_tf, scores_tf, multi_tf = orch.process()
            assert multi_tf is None
            with open(results_tf) as f:
                rows = json.load(f)
            # Row schema is base identity columns + every scorer's columns.
            assert set(rows[0].keys()) == set(orch.columns)
            row = rows[0]
            assert row["mcp_readability_endpoint_type"] == "PROD"
            assert int(row["mcp_readability_total_tools"]) == 2
            assert int(row["mcp_readability_p1_issues"]) == 1
            # The numeric score stays on its own metric column (row-type
            # discriminator)...
            assert int(row["mcp_readability_score"]) == 80
            # ...but neither feedback column reports it.
            feedback_html = row["mcp_readability_llm_feedback_html"]
            assert "readability score" not in feedback_html.lower()
            assert "Allowed exceptions (waived)" in feedback_html
            assert "use-enums" in feedback_html  # waiver surfaced from _FakeLLM
            feedback_json = json.loads(row["mcp_readability_llm_feedback_json"])
            assert "readability_score" not in feedback_json
            assert feedback_json["waived"][0]["rule_id"] == "use-enums"
            assert row["job_id"] == job_id

            # scores_tf: one row per (endpoint, scorer).
            with open(scores_tf) as f:
                scores = json.load(f)
            assert len(scores) == 2 * len(rows)
            by_comp = {s["comparator"]: s for s in scores}
            assert set(by_comp) == {"mcp_tool_metrics", "mcp_style_readability"}
            # readability: no P0 -> pass; metrics: within budget -> pass.
            assert by_comp["mcp_style_readability"]["score"] == 100
            assert by_comp["mcp_tool_metrics"]["score"] == 100
            assert (
                by_comp["mcp_style_readability"]["id"]
                == row["mcp_readability_product_name"]
            )
            assert by_comp["mcp_style_readability"]["comparison_error"] is None


def test_orchestrator_fetch_error_aborts_run():
    """Fail-fast: a fetch failure propagates and aborts the run (nothing stored).

    There is no per-endpoint status; the error surfaces to the caller (and the
    run log) instead of being recorded as a row.
    """
    with patch(
        "scorers.mcp_style_readability.get_generator", return_value=_FakeLLM()
    ):
        from evaluator import get_orchestrator

        with tempfile.TemporaryDirectory() as d:
            ep_path = os.path.join(d, "endpoints.yaml")
            _write_endpoints(
                ep_path,
                "Bad",
                {"type": "file", "path": os.path.join(d, "missing.json")},
            )
            orch = get_orchestrator(_base_config(ep_path, d), [], {})
            with pytest.raises(McpToolsError):
                orch.evaluate([])


def test_unknown_scorer_raises():
    """An unregistered scorer name in the run config fails fast at construction."""
    from evaluator import get_orchestrator

    with tempfile.TemporaryDirectory() as d:
        ep_path = os.path.join(d, "endpoints.yaml")
        _write_endpoints(
            ep_path,
            "Sample",
            {"type": "file", "path": _ds("datasets/mcp_readability/sample_tools.json")},
        )
        config = _base_config(ep_path, d)
        config["scorers"] = {"bogus_scorer": {}}
        with pytest.raises(ValueError):
            get_orchestrator(config, [], {})


def test_missing_scorers_block_raises():
    from evaluator import get_orchestrator

    with tempfile.TemporaryDirectory() as d:
        ep_path = os.path.join(d, "endpoints.yaml")
        _write_endpoints(
            ep_path,
            "Sample",
            {"type": "file", "path": _ds("datasets/mcp_readability/sample_tools.json")},
        )
        config = _base_config(ep_path, d)
        del config["scorers"]
        with pytest.raises(ValueError):
            get_orchestrator(config, [], {})


def test_endpoint_type_filter():
    """endpoint_types filter matches case-insensitively; unknowns fail fast."""
    orch = McpReadabilityOrchestrator.__new__(McpReadabilityOrchestrator)
    # Built the way __init__ builds it: validated canonical upper-case names.
    orch.endpoint_type_filter = {_validate_endpoint_type("prod")}
    orch.endpoints = [
        {"product_name": "A", "endpoint_type": "PROD"},
        {"product_name": "B", "endpoint_type": "DEV"},
    ]
    kept = orch._filtered_endpoints()
    assert [e["product_name"] for e in kept] == ["A"]


# --------------------------------------------------------------------------
# Integration with the shared evalbench.py report path (no changes needed there)
# --------------------------------------------------------------------------
def test_datasetless_config_loads_empty_dataset():
    """A run config with no dataset_config loads an empty dataset (no KeyError).

    This is what lets evalbench.py's unchanged
    `load_dataset_from_json(session["dataset_config"], config)` work for the
    datasetless mcp_readability orchestrator.
    """
    from util.config import set_session_configs
    from dataset.dataset import load_dataset_from_json, flatten_dataset

    session = {}
    set_session_configs(session, {"orchestrator": "mcp_readability"})
    assert session["dataset_config"] is None
    dataset = load_dataset_from_json(session["dataset_config"], {})
    assert flatten_dataset(dataset) == []


def test_scores_flow_through_shared_analyzer():
    """The emitted score rows are consumable by the shared analyzer as-is.

    Proves evalbench.py's standard branch (`analyzer.analyze_result`) produces a
    correct per-scorer pass rate from mcp_readability's scores_tf without any
    mcp-specific handling. Each scorer key under `scorers:` aggregates its own
    `comparator` rows.
    """
    import reporting.analyzer as analyzer

    def _score(id_, comparator, score):
        return {
            "id": id_,
            "comparator": comparator,
            "score": score,
            "comparison_logs": "",
            "comparison_error": None,
        }

    # Endpoint A: readability pass (no P0), within budget.
    # Endpoint B: readability fail (P0), within budget.
    scores = [
        _score("A", "mcp_style_readability", 100),
        _score("A", "mcp_tool_metrics", 100),
        _score("B", "mcp_style_readability", 0),
        _score("B", "mcp_tool_metrics", 100),
    ]
    config = {"scorers": {"mcp_style_readability": None, "mcp_tool_metrics": None}}

    _, summary_df = analyzer.analyze_result(
        scores, config, num_prompts=0, num_trials=1
    )
    readability = summary_df[
        summary_df["metric_name"] == "mcp_style_readability"
    ].iloc[0]
    assert int(readability["correct_results_count"]) == 1  # only A is P0-clean
    assert int(readability["total_results_count"]) == 2

    metrics = summary_df[summary_df["metric_name"] == "mcp_tool_metrics"].iloc[0]
    assert int(metrics["correct_results_count"]) == 2  # both within budget
    assert int(metrics["total_results_count"]) == 2
