import os
import sys
from unittest.mock import MagicMock, patch

# Add parent directory to path so we can import generators.
sys.path.append(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from generators.models.agent_runtime import (
    _extract_sql,
    _parse_stream_response,
    AgentRuntimeGenerator,
)


# Unit tests for helper functions.
def test_parse_stream_response_accumulates_text():
    # Mock gRPC chunks returned by the stream client.
    chunk1 = MagicMock()
    chunk1.data = b'{"content": {"parts": [{"text": "SELECT "}]}}'
    
    chunk2 = MagicMock()
    chunk2.data = (
        b'{"content": {"parts": [{"text": "1 FROM "}]}}\n'
        b'{"content": {"parts": [{"text": "table;"}]}}'
    )

    response_stream = [chunk1, chunk2]
    
    result = _parse_stream_response(response_stream)
    assert result == "SELECT 1 FROM table;"


def test_parse_stream_response_handles_invalid_chunks():
    # Test that decoding or JSON errors in one chunk don't crash the loop.
    chunk_valid = MagicMock()
    chunk_valid.data = b'{"content": {"parts": [{"text": "SELECT * "}]}}'
    
    chunk_invalid = MagicMock()
    chunk_invalid.data = b'invalid-non-json-bytes'
    
    response_stream = [chunk_valid, chunk_invalid]
    result = _parse_stream_response(response_stream)
    assert result == "SELECT * "


def test_extract_sql_from_clean_json():
    text = """```json
{
  "explain": "Retrieves values.",
  "sql": "SELECT * FROM users;"
}
```"""
    assert _extract_sql(text) == "SELECT * FROM users;"


def test_extract_sql_from_mixed_response():
    # Mix of raw SQL followed by markdown block.
    text = """SELECT * FROM users;
```json
{
  "explain": "Retrieves values.",
  "sql": "SELECT * FROM users;"
}
```"""
    assert _extract_sql(text) == "SELECT * FROM users;"


def test_extract_sql_from_plain_markdown_fallback():
    # Fallback when no JSON envelope can be parsed, splits by block wrapper.
    text = """SELECT * FROM users;
```
Explanation of query below...
```"""
    assert _extract_sql(text) == "SELECT * FROM users;"


def test_extract_sql_raw_fallback():
    # Fallback to trim when no markdown blocks are present.
    text = "  SELECT * FROM users;  "
    assert _extract_sql(text) == "SELECT * FROM users;"


# Unit tests for Generator class.
@patch("generators.models.agent_runtime.vertexai.init")
@patch("generators.models.agent_runtime.agent_engines.AgentEngine")
def test_generator_initialization(mock_agent_engine, mock_vertexai_init):
    config = {
        "resource_name": "projects/p/locations/l/reasoningEngines/r",
        "gcp_project_id": "test-project",
        "gcp_region": "us-central1"
    }

    generator = AgentRuntimeGenerator(config)

    # Assert Vertex AI is initialized correctly.
    mock_vertexai_init.assert_called_once_with(
        project="test-project", location="us-central1"
    )

    # Assert AgentEngine is instantiated with correct resource ID.
    mock_agent_engine.assert_called_once_with(
        "projects/p/locations/l/reasoningEngines/r"
    )
    assert generator.remote_app == mock_agent_engine.return_value
    assert generator.name == "agent_runtime"


@patch("generators.models.agent_runtime.vertexai.init")
@patch("generators.models.agent_runtime.agent_engines.AgentEngine")
def test_generate_internal_queries_stream_api(mock_agent_engine, mock_vertexai_init):
    config = {
        "resource_name": "projects/p/locations/l/reasoningEngines/r",
        "gcp_project_id": "test-project",
        "gcp_region": "us-central1"
    }
    
    # Setup mock clients and streaming responses.
    mock_client = MagicMock()
    mock_agent_engine.return_value.execution_api_client = mock_client
    
    mock_chunk = MagicMock()
    mock_chunk.data = b'{"content": {"parts": [{"text": "SELECT 5;"}]}}'
    mock_client.stream_query_reasoning_engine.return_value = [mock_chunk]

    generator = AgentRuntimeGenerator(config)
    sql = generator.generate_internal("run query 5")

    # Verify stream query is called with correct name, prompts, method.
    mock_client.stream_query_reasoning_engine.assert_called_once()
    call_args = (
        mock_client.stream_query_reasoning_engine.call_args[1]["request"]
    )
    
    assert call_args.name == "projects/p/locations/l/reasoningEngines/r"
    assert call_args.input["message"] == "run query 5"
    assert call_args.class_method == "stream_query"

    # Verify query output was parsed and returned.
    assert sql == "SELECT 5;"
