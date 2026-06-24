import json
import os
import sys
import tempfile
from unittest.mock import patch

import pandas as pd
import pytest

sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# Repo root (two levels up from evalbench/evalbench/test/) so dataset paths
# resolve regardless of the pytest working directory.
_REPO_ROOT = os.path.dirname(
    os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
)


def _ds(rel):
    return os.path.join(_REPO_ROOT, rel)

from evaluator.mcp_readability.enums import (
    CheckStatus,
    EndpointType,
    Environment,
    coerce_endpoint_type,
    coerce_environment,
)
from evaluator.mcp_readability import exceptions as exc_mod
from evaluator.mcp_readability.token_estimator import (
    estimate_tokens,
    token_budget_used_percent,
)
from mcp import types as mcp_types
from generators.models.mcp_tools import McpToolsGenerator, McpToolsError
from generators.models.mcp_tool_formatter import format_tools_to_man_page
from scorers.mcp_style_compliance import McpStyleComplianceScorer
from reporting.mcp_readability_csv import COLUMNS, write_compliance_csv


# --------------------------------------------------------------------------
# Enums
# --------------------------------------------------------------------------
def test_enum_coercion():
    assert coerce_endpoint_type("REMOTE") is EndpointType.REMOTE
    assert coerce_endpoint_type("local") is EndpointType.LOCAL
    assert coerce_endpoint_type("bogus") is EndpointType.ENDPOINT_TYPE_UNSPECIFIED
    assert coerce_endpoint_type(None) is EndpointType.ENDPOINT_TYPE_UNSPECIFIED
    assert coerce_environment("autopush") is Environment.AUTOPUSH
    assert coerce_environment("PROD") is Environment.PROD


def test_check_status_members():
    # check_status reflects whether the eval ran, not compliance findings.
    assert {s.name for s in CheckStatus} == {
        "CHECK_STATUS_UNSPECIFIED",
        "SUCCESS",
        "FETCH_ERROR",
        "ANALYSIS_ERROR",
        "INTERNAL_ERROR",
    }


# --------------------------------------------------------------------------
# Token estimation
# --------------------------------------------------------------------------
def test_token_math():
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1
    assert token_budget_used_percent(0, 0) == 0.0
    assert token_budget_used_percent(250, 25000) == 1.0
    assert token_budget_used_percent(210, 25000) == 0.84


# --------------------------------------------------------------------------
# Exceptions matching
# --------------------------------------------------------------------------
def test_exceptions_matching():
    all_exc = [
        {"endpoint_url": "https://a/mcp", "rule_id": "R1", "reason": "x"},
        {"environment": "AUTOPUSH", "rule_id": "R2", "reason": "y"},
        {"rule_id": "R3", "reason": "global"},  # match-all
        {"reason": "no rule id, ignored"},
    ]
    ep = {"endpoint_url": "https://a/mcp", "environment": "PROD"}
    matched = {e["rule_id"] for e in exc_mod.applicable_exceptions(ep, all_exc)}
    assert matched == {"R1", "R3"}

    ep2 = {"endpoint_url": "https://b/mcp", "environment": "AUTOPUSH"}
    matched2 = {e["rule_id"] for e in exc_mod.applicable_exceptions(ep2, all_exc)}
    assert matched2 == {"R2", "R3"}


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
# Formatter (man-page markup)
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
    mpr = format_tools_to_man_page([tool])
    assert mpr.total_tools == 1
    assert "TOOL: t" in mpr.man_page
    assert "enum:" in mpr.man_page and '"a"' in mpr.man_page
    assert "default:" in mpr.man_page


# --------------------------------------------------------------------------
# Generator (file source)
# --------------------------------------------------------------------------
def test_generator_file_source():
    gen = McpToolsGenerator({})
    endpoint = {
        "tools_source": {
            "type": "file",
            "path": _ds("datasets/mcp_readability/sample_tools.yaml"),
        }
    }
    tools, mpr = gen.fetch_tools(endpoint, {})
    assert mpr.total_tools == 3
    assert all(isinstance(t, mcp_types.Tool) for t in tools)
    assert {t.name for t in tools} == {"list_datasets", "RunQuery", "delete_table"}
    assert "TOOL: RunQuery" in mpr.man_page


def test_generator_missing_file_raises():
    gen = McpToolsGenerator({})
    with pytest.raises(McpToolsError):
        gen.fetch_tools({"tools_source": {"type": "file", "path": "nope.yaml"}}, {})


def test_generator_mcp_source_mocked():
    """The live `mcp` path runs the SDK fetch; mock it so no network is needed."""
    gen = McpToolsGenerator({})

    async def _fake_fetch(self, url, headers):
        assert url == "https://example.com/mcp"
        return [
            mcp_types.Tool(
                name="t1",
                description="d",
                inputSchema={"type": "object", "properties": {}},
            )
        ]

    with patch.object(McpToolsGenerator, "_async_fetch_tools", _fake_fetch):
        endpoint = {"endpoint_url": "example.com", "tools_source": {"type": "mcp"}}
        tools, mpr = gen.fetch_tools(endpoint, {})
        assert [t.name for t in tools] == ["t1"]
        assert mpr.total_tools == 1


# --------------------------------------------------------------------------
# Scorer parsing / html (no LLM call)
# --------------------------------------------------------------------------
def test_scorer_parse_and_html():
    raw = """```json
    {"compliance_score": 75, "findings": [
       {"severity": "P0", "rule_id": "P0-X", "tool": "t", "message": "m", "suggestion": "s"},
       {"severity": "P2", "rule_id": "P2-Y", "tool": "t", "message": "m", "suggestion": "s"}
     ], "waived": [], "summary": "ok"}
    ```"""
    # Build a scorer without invoking the LLM constructor path.
    scorer = McpStyleComplianceScorer.__new__(McpStyleComplianceScorer)
    fb = scorer._parse(raw)
    assert fb["p0_issues"] == 1
    assert fb["p2_issues"] == 1
    assert fb["compliance_score"] == 75
    html = McpStyleComplianceScorer.to_html(fb)
    assert "<table" in html and "P0-X" in html


def test_scorer_consistency_second_pass():
    """With previous_feedback, evaluate() runs a 2nd reconciliation pass."""

    class _SeqLLM:
        def __init__(self, responses):
            self.responses = responses
            self.calls = 0

        def generate(self, prompt):
            r = self.responses[min(self.calls, len(self.responses) - 1)]
            self.calls += 1
            return r

    latest = json.dumps(
        {"compliance_score": 50, "findings": [
            {"severity": "P0", "rule_id": "New", "tool": "", "message": "m",
             "suggestion": "s"}], "summary": "latest"}
    )
    merged = json.dumps(
        {"compliance_score": 60, "findings": [
            {"severity": "P1", "rule_id": "Old", "tool": "t", "message": "m",
             "suggestion": "s"}], "summary": "merged"}
    )
    scorer = McpStyleComplianceScorer.__new__(McpStyleComplianceScorer)
    scorer.model = _SeqLLM([latest, merged])

    fb = scorer.evaluate(
        tools_markup="x",
        style_guide="g",
        product_name="p",
        previous_feedback='{"findings": [{"severity": "P1", "rule_id": "Old"}]}',
    )
    assert scorer.model.calls == 2  # review + consistency
    assert fb["summary"] == "merged"
    assert fb["p1_issues"] == 1 and fb["p0_issues"] == 0


def test_scorer_no_previous_feedback_single_pass():
    class _OneLLM:
        def __init__(self):
            self.calls = 0

        def generate(self, prompt):
            self.calls += 1
            return json.dumps({"compliance_score": 90, "findings": [], "summary": "ok"})

    scorer = McpStyleComplianceScorer.__new__(McpStyleComplianceScorer)
    scorer.model = _OneLLM()
    fb = scorer.evaluate(tools_markup="x", style_guide="g", product_name="p")
    assert scorer.model.calls == 1
    assert fb["compliance_score"] == 90


# --------------------------------------------------------------------------
# CSV reporter schema
# --------------------------------------------------------------------------
def test_csv_schema_and_types():
    rows = [
        {
            "product_name": "p",
            "endpoint_url": "u",
            "endpoint_type": "REMOTE",
            "environment": "PROD",
            "check_timestamp": "2026-01-01T00:00:00",
            "check_status": "SUCCESS",
            "p0_issues": 0,
            "p1_issues": 1,
            "p2_issues": 2,
            "total_tools": 3,
            "estimated_tokens": 210,
            "token_budget_used_percent": 0.84,
            "compliance_score": 90,
            "llm_feedback_json": "{}",
            "llm_feedback_html": "<div></div>",
            "error_message": "",
        }
    ]
    with tempfile.TemporaryDirectory() as d:
        path = write_compliance_csv(rows, d, "job1")
        df = pd.read_csv(path)
        assert list(df.columns) == COLUMNS
        assert df.iloc[0]["p1_issues"] == 1
        assert abs(df.iloc[0]["token_budget_used_percent"] - 0.84) < 1e-9


# --------------------------------------------------------------------------
# End-to-end orchestrator with stubbed LLM
# --------------------------------------------------------------------------
class _FakeLLM:
    def generate(self, prompt):
        return json.dumps(
            {
                "compliance_score": 80,
                "findings": [
                    {"severity": "P1", "rule_id": "P1-A", "tool": "RunQuery",
                     "message": "m", "suggestion": "s"},
                ],
                "waived": [{"rule_id": "P2-TOOL-DESC-LENGTH", "reason": "r"}],
                "summary": "fine",
            }
        )


def test_orchestrator_end_to_end():
    import yaml

    with patch(
        "scorers.mcp_style_compliance.get_generator", return_value=_FakeLLM()
    ):
        from evaluator import get_orchestrator

        # Use a self-contained offline endpoints file (file source) so the test
        # stays deterministic and independent of the production endpoint list.
        with tempfile.TemporaryDirectory() as d:
            ep_path = os.path.join(d, "endpoints.yaml")
            with open(ep_path, "w") as f:
                yaml.safe_dump(
                    {
                        "defaults": {
                            "endpoint_type": "REMOTE",
                            "tools_source": {"type": "file"},
                        },
                        "endpoints": [
                            {
                                "product_name": "Sample",
                                "endpoint_url": "file://sample",
                                "environment": "DEV",
                                "tools_source": {
                                    "type": "file",
                                    "path": _ds(
                                        "datasets/mcp_readability/sample_tools.yaml"
                                    ),
                                },
                            }
                        ],
                    },
                    f,
                )
            config = {
                "orchestrator": "mcp_readability",
                "endpoints_config": ep_path,
                "style_guide": _ds("datasets/mcp_readability/style_guide.md"),
                "exceptions_config": _ds("datasets/mcp_readability/exceptions.yaml"),
                "tools_generator_config": _ds(
                    "datasets/mcp_readability/tools_generator.yaml"
                ),
                "token_budget": 25000,
                "scorers": {"mcp_style_compliance": {"model_config": "unused"}},
                "runners": {"endpoint_runners": 2},
                "reporting": {"csv": {"output_directory": d}},
            }
            orch = get_orchestrator(config, [], {})
            orch.evaluate([])
            job_id, _, a, b, c = orch.process()
            assert (a, b, c) == (None, None, None)
            df = pd.read_csv(
                os.path.join(d, job_id, "mcp_readability_compliance.csv")
            )
            assert list(df.columns) == COLUMNS
            row = df.iloc[0]
            assert row["check_status"] == "SUCCESS"
            assert row["endpoint_type"] == "REMOTE"  # inherited from defaults
            assert int(row["total_tools"]) == 3
            assert int(row["p1_issues"]) == 1
            assert int(row["compliance_score"]) == 80


def test_orchestrator_fetch_error():
    import yaml

    with patch(
        "scorers.mcp_style_compliance.get_generator", return_value=_FakeLLM()
    ):
        from evaluator import get_orchestrator

        with tempfile.TemporaryDirectory() as d:
            ep_path = os.path.join(d, "endpoints.yaml")
            with open(ep_path, "w") as f:
                yaml.safe_dump(
                    {
                        "defaults": {"tools_source": {"type": "file"}},
                        "endpoints": [
                            {
                                "product_name": "Bad",
                                "endpoint_url": "x",
                                "endpoint_type": "REMOTE",
                                "environment": "PROD",
                                "tools_source": {
                                    "type": "file",
                                    "path": os.path.join(d, "missing.yaml"),
                                },
                            }
                        ],
                    },
                    f,
                )
            config = {
                "orchestrator": "mcp_readability",
                "endpoints_config": ep_path,
                "style_guide": _ds("datasets/mcp_readability/style_guide.md"),
                "tools_generator_config": _ds(
                    "datasets/mcp_readability/tools_generator.yaml"
                ),
                "token_budget": 25000,
                "scorers": {"mcp_style_compliance": {"model_config": "unused"}},
                "reporting": {"csv": {"output_directory": d}},
            }
            orch = get_orchestrator(config, [], {})
            orch.evaluate([])
            job_id, _, _, _, _ = orch.process()
            df = pd.read_csv(
                os.path.join(d, job_id, "mcp_readability_compliance.csv")
            )
            row = df.iloc[0]
            assert row["check_status"] == "FETCH_ERROR"
            assert "not found" in str(row["error_message"]).lower()
