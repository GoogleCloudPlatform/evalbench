# Judge Tools (Function Calling)

LLM-judged scorers (`BinaryRubricScorer`, `LlmRater`, `GoalCompletionRate`,
`BehavioralMetrics`, `ParameterAnalysis`, `SkillsBestPractices`) call
`judge.generate(prompt)` and parse the text back. By default the judge is
single-shot: it sees the prompt and produces an answer with no access to
external state.

Tool support lets a judge **invoke registered tools mid-generation** so it can
ground its answer in something the prompt alone can't tell it — for example,
fetching `https://beam.apache.org/get-started/downloads/` to check that an
agent named the most-recent Apache Beam version.

> **Status:** Gemini (`gcp_vertex_gemini`) only. Claude SDK judge support is
> tracked as a follow-up.

## Activation

Tools are **opt-in per judge** via a `tools:` list in the model config YAML.
Configs without the key keep the original single-shot codepath.

```yaml
generator: gcp_vertex_gemini
vertex_model: gemini-2.5-pro
base_prompt: ""
execs_per_minute: 5
tools:
  - fetch_url
```

Unknown tool names fail fast at `GeminiGenerator.__init__` with a `ValueError`
that lists the available tools.

## Available tools

| Name | Purpose | Constraints |
|---|---|---|
| `fetch_url` | Fetch an HTTPS URL and return its body as text. HTML is stripped to plain text. | HTTPS only; SSRF guard against private/loopback/link-local/multicast/reserved IPs; 10s timeout; 50 KB body cap. |

`fetch_url` returns `"Error: ..."` strings on any failure (bad scheme, blocked
host, HTTP error, timeout) so the model can observe the failure and react
rather than crash the judge.

## End-to-end example

**`datasets/model_configs/gemini_2.5_pro_with_tools_model.yaml`** (new)

```yaml
generator: gcp_vertex_gemini
vertex_model: gemini-2.5-pro
base_prompt: ""
execs_per_minute: 5
tools:
  - fetch_url
```

**`datasets/your-eval/example_run_config.yaml`**

```yaml
dataset_config: datasets/your-eval/scenarios.evalset.json
dataset_format: agent-format
orchestrator: agent
model_config: datasets/model_configs/gemini_cli_model.yaml
simulated_user_model_config: datasets/model_configs/gemini_2.5_pro_model.yaml

scorers:
  binary_rubric_scorer:
    model_config: datasets/model_configs/gemini_2.5_pro_with_tools_model.yaml
  goal_completion:
    model_config: datasets/model_configs/gemini_2.5_pro_model.yaml   # no tools
  trajectory_matcher: {}
  turn_count: {}

reporting:
  csv:
    output_directory: 'results'
```

**`datasets/your-eval/scenarios.evalset.json`** (rubric lives on the scenario)

```json
{
  "scenarios": [
    {
      "id": "beam-version-check",
      "starting_prompt": "What is the most recent version of Apache Beam?",
      "conversation_plan": "User wants the agent to look up the current Beam release.",
      "binary_rubric": [
        "Did the agent's final answer state the most-recent Apache Beam version listed on https://beam.apache.org/get-started/downloads/?"
      ],
      "max_turns": 2
    }
  ]
}
```

## How it threads through

1. The run config's `scorers.binary_rubric_scorer.model_config` is loaded via
   `get_generator(...)` inside `BinaryRubricScorer.__init__`
   (`evalbench/scorers/binaryrubricscorer.py`).
2. That YAML has `generator: gcp_vertex_gemini` plus `tools: [fetch_url]`, so
   `GeminiGenerator` resolves the names through
   `evalbench/generators/models/tools/__init__.py::get_tools(...)` and builds
   the native `types.Tool` config once at construction.
3. When the scorer calls `self.model.generate(prompt)`, the judge enters the
   tool-calling loop. If the model emits a `function_call`, the registered
   tool runs, its result is appended as a `FunctionResponse`, and the loop
   continues until the model returns a final text answer.
4. The loop is bounded at `MAX_TOOL_ITERATIONS = 5`
   (`evalbench/generators/models/gemini.py`). On exhaust the judge returns
   the last text and logs a warning.

Per-scorer judges are independent, so you can give `fetch_url` only to the
rubric judge and leave latency-sensitive judges (`behavioral_metrics`,
`parameter_analysis`) on plain single-shot configs.

## Security notes

- **HTTPS-only.** Other schemes (`http`, `file`, ...) are rejected before any
  network call.
- **SSRF guard.** `fetch_url` resolves the hostname and rejects responses
  pointing at private / loopback / link-local / multicast / reserved address
  ranges (Python's `ipaddress` classification).
- **Redirects.** Each redirect target gets the same checks as the first URL.
  A refused redirect stops the request.
- **Connection address.** The connection resolves the host name one time,
  checks each address, and connects to one of those addresses. A DNS answer
  that changes after the URL check does not reach a private address.
- **No proxy.** The tool ignores proxy settings such as `HTTPS_PROXY`,
  because a proxy resolves the host name itself.

## Adding a new tool

1. Create `evalbench/generators/models/tools/<my_tool>.py` exporting a
   `Tool` instance:

   ```python
   from .base import Tool

   def _impl(args: dict) -> str:
       return f"got args: {args}"

   MY_TOOL = Tool(
       name="my_tool",
       description="One-sentence summary the model will read.",
       input_schema={
           "type": "object",
           "properties": {"x": {"type": "string"}},
           "required": ["x"],
       },
       fn=_impl,
   )
   ```

2. Register it in `evalbench/generators/models/tools/__init__.py`:

   ```python
   from .my_tool import MY_TOOL

   TOOL_REGISTRY = {
       FETCH_URL_TOOL.name: FETCH_URL_TOOL,
       MY_TOOL.name: MY_TOOL,
   }
   ```

3. Reference it in any judge model config:

   ```yaml
   tools:
     - fetch_url
     - my_tool
   ```

Tool implementations should return strings (errors as `"Error: ..."`), keep
side effects bounded, and avoid raising — exceptions are caught and converted
into `"Error: <ExcType>: <message>"` strings so the loop can continue, but a
well-behaved tool produces actionable error text itself.
