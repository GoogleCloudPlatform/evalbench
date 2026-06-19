"""Prompt template for the MCP compliance-check judge LLM."""

JUDGE_PROMPT = """\
You are an expert reviewer of MCP (Model Context Protocol) tool surfaces.
Your job is to grade a tool bundle against a compliance guide.

# Compliance guide

The guide below defines the rules to check, each tagged with a severity.
Use ONLY the severities defined in the guide (typically P0 / P1 / P2). Do
not invent new severities.

--- BEGIN GUIDE ---
{guide_md}
--- END GUIDE ---

# Tool bundle to review

Each tool below is rendered as YAML. `parameters.*.required` is a boolean,
and `enum` / `default` / `format` / `pattern` / `examples` / `minimum` /
`maximum` are pulled up to first-class fields when present in the original
JSON Schema.

--- BEGIN BUNDLE ---
{bundle_yaml}
--- END BUNDLE ---

# Output contract

Return ONLY a single JSON object with this exact top-level shape (no
markdown code fences, no commentary before or after):

{{
  "general": {{
    "P0": [{{"title": "...", "description": "..."}}],
    "P1": [],
    "P2": []
  }},
  "per_tool": {{
    "<tool_name>": {{
      "P0": [{{"title": "...", "description": "..."}}],
      "P1": [],
      "P2": []
    }}
  }}
}}

Rules:
- `general` is for issues that span multiple tools or the bundle as a
  whole (naming convention drift, missing pagination pattern across all
  list tools, etc.).
- `per_tool` keys must be tool names exactly as they appear in the bundle.
- Each finding has a short `title` (sentence case, no trailing period,
  ideally ≤ 80 chars) and a `description` (1-3 sentences, ending with a
  concrete suggestion when possible).
- Severity lists may be empty `[]` but every severity key from the guide
  must be present.
- Tools with no findings may be omitted from `per_tool`.
- Output valid JSON. No trailing commas. No comments. No prose outside
  the JSON object.
"""


def build_bundle_yaml(tool_yaml: dict[str, str]) -> str:
    """Concatenate the per-tool YAMLs into one bundle the judge consumes.

    Separator is a YAML document break so the LLM treats them as distinct
    documents but they remain in one prompt.
    """
    chunks: list[str] = []
    for name, yml in tool_yaml.items():
        chunks.append(f"# --- tool: {name} ---\n{yml.rstrip()}\n")
    return "\n---\n".join(chunks)


def build_prompt(guide_md: str, tool_yaml: dict[str, str]) -> str:
    return JUDGE_PROMPT.format(
        guide_md=guide_md.strip(),
        bundle_yaml=build_bundle_yaml(tool_yaml).strip(),
    )
