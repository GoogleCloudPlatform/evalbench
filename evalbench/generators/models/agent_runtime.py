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

from google.api_core.exceptions import ResourceExhausted
from google.cloud.aiplatform_v1.types import (
    reasoning_engine_execution_service as aip_types,
)
import vertexai
from vertexai import agent_engines

from generators.models.generator import QueryGenerator
from util.gcp import get_gcp_project, get_gcp_region
from util.rate_limit import ResourceExhaustedError
from util.sanitizer import sanitize_sql


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
    # 1. Try to locate and extract a JSON envelope anywhere in the text first.
    json_match = re.search(r"```json\s*(\{.*?\})\s*```", text, re.DOTALL)
    if json_match:
        try:
            data = json.loads(json_match.group(1).strip())
            if isinstance(data, dict) and "sql" in data:
                return sanitize_sql(str(data["sql"]))
        except json.JSONDecodeError:
            pass

    # 2. Try to locate any markdown code block (e.g. ```sql or ```)
    sql_match = (
        re.search(r"```(?:sql)?\s*(.*?)\s*```", text, re.DOTALL | re.IGNORECASE)
    )
    if sql_match:
        return sanitize_sql(sql_match.group(1).strip())

    # 3. Fallback: Treat as raw SQL query
    return sanitize_sql(text)


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
            get_gcp_project(querygenerator_config.get("gcp_project_id"))
        )
        location = get_gcp_region(querygenerator_config.get("gcp_region"))
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

        except ResourceExhausted as e:
            raise ResourceExhaustedError(e)
        except Exception as e:
            logging.exception(
                f"Error querying remote Agent Runtime: {e}"
            )
            return ""
