"""Agent Runtime (Gemini Enterprise Agent Platform) generator for EvalBench.

The remote Agent Engine instance is expected to return the generated query
in one of the following formats:

1. A raw SQL query string, optionally wrapped in markdown code blocks:
   SELECT * FROM table;

2. A JSON envelope wrapped in a ```json block containing a "sql" key:
   ```json
   {
     "sql": "SELECT * FROM table;",
     "explain": "Optional explanation of the query..."
   }
   ```
"""

import json
import logging
import os
import re

from google.cloud.aiplatform_v1.types import (
    reasoning_engine_execution_service as aip_types,
)
import vertexai
from vertexai import agent_engines

from generators.models.generator import QueryGenerator


def _parse_stream_response(response) -> str:
    """Accumulates text chunks from the streaming response."""
    complete_text = ""
    for chunk in response:
        data = getattr(chunk, "data", b"")
        if not data:
            continue
        try:
            utf8_data = data.decode("utf-8")
            for line in utf8_data.split("\n"):
                if not line.strip():
                    continue
                parsed_json = json.loads(line)
                parts = parsed_json.get("content", {}).get("parts", [])
                for part in parts:
                    text = part.get("text")
                    if text:
                        complete_text += text
        except (UnicodeDecodeError, json.JSONDecodeError):
            # Fallback if chunk payload is not valid JSON.
            continue
    return complete_text


def _extract_sql(text: str) -> str:
    """Extracts SQL query from JSON envelope or markdown blocks in text."""
    # Try to locate and extract a JSON envelope anywhere in the text first.
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1).strip())
            if isinstance(data, dict) and "sql" in data:
                return str(data["sql"])
        except json.JSONDecodeError:
            pass

    # Fallback: Split by markdown blocks and take the first segment.
    if "```" in text:
        candidate = text.split("```")[0].strip()
        if candidate:
            return candidate

    return text.strip()


class AgentRuntimeGenerator(QueryGenerator):
    """Generates SQL queries using Agent Runtime."""

    def __init__(self, querygenerator_config):
        super().__init__(querygenerator_config)
        self.name = "agent_runtime"
        self.resource_name = (
            querygenerator_config.get("resource_name")
            or os.environ.get("AGENT_ENGINE_RESOURCE")
        )
        if not self.resource_name:
            raise ValueError(
                "AgentRuntimeGenerator requires `resource_name` in model "
                "config YAML or AGENT_ENGINE_RESOURCE env variable."
            )

        project_id = (
            querygenerator_config.get("gcp_project_id")
            or os.environ.get("GOOGLE_CLOUD_PROJECT")
        )
        if not project_id:
            raise ValueError(
                "AgentRuntimeGenerator requires `gcp_project_id` in model "
                "config YAML or GOOGLE_CLOUD_PROJECT env variable."
            )

        location = (
            querygenerator_config.get("gcp_region")
            or os.environ.get("GOOGLE_CLOUD_REGION")
            or os.environ.get("GOOGLE_CLOUD_LOCATION")
        )
        if not location:
            raise ValueError(
                "AgentRuntimeGenerator requires `gcp_region` in model config "
                "YAML or GOOGLE_CLOUD_REGION/LOCATION env vars."
            )
        logging.info(
            "Initializing Vertex AI (Project: %s, Location: %s)",
            project_id,
            location,
        )
        vertexai.init(project=project_id, location=location)

        logging.info(
            "Connecting to live Agent Runtime: "
            f"{self.resource_name}"
        )
        self.remote_app = agent_engines.AgentEngine(self.resource_name)

    def generate_internal(self, prompt: str) -> str:
        """Queries remote agent endpoint and extracts SQL reply string."""
        try:
            # Query the agent using the raw streaming endpoint.
            client = self.remote_app.execution_api_client
            response = client.stream_query_reasoning_engine(
                request=aip_types.StreamQueryReasoningEngineRequest(
                    name=self.resource_name,
                    input={
                        "message": prompt,
                        "user_id": "evalbench_user",
                    },
                    class_method="stream_query",
                ),
            )

            complete_text = _parse_stream_response(response)
            return _extract_sql(complete_text)

        except Exception as e:
            logging.exception(
                f"Error querying remote Agent Runtime: {e}"
            )
            return ""
