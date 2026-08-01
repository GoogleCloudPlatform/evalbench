# Agentic Evaluation Items Configuration

This JSON evalset file defines the scenarios for a multi-turn [agentic evaluation](/docs/agentic-evals.md). Each scenario is one agentic user journey: a starting prompt, a plan for the simulated user to follow, and the tool calls the agent is expected to make.

For single-turn NL2SQL evaluations, see the [NL2SQL dataset format](/docs/configs/dataset-config.md) instead.

Select this format by setting `dataset_format` in your [run config](/docs/configs/run-config.md) to `agent-format` (orchestrator `agent`) or `gemini-cli-format` (orchestrator `geminicli`).

## File Structure

The file is a single JSON object with a `scenarios` array:

```json
{
  "scenarios": [
    {
      "id": "unique-scenario-id",
      "starting_prompt": "The initial user message",
      "conversation_plan": "Instructions for the simulated user...",
      "expected_trajectory": ["cloud-sql__list_instances"],
      "env": {
        "GOOGLE_CLOUD_PROJECT": "my-project"
      },
      "kind": "tools",
      "max_turns": 6
    }
  ]
}
```

## Scenario Fields

| **Key** | **Required** | **Description** |
| ------- | ------------ | --------------- |
| `id` | Yes | A unique identifier for the scenario. Also used to select scenarios via `scenarios` / `scenario_pattern` in the run config. |
| `starting_prompt` | Yes | The first user message sent to the agent. |
| `conversation_plan` | Yes | Natural language instructions guiding the simulated user's behavior across turns — the goal, what information to supply when asked, and how to react to agent responses. See [Writing Good Conversation Plans](#writing-good-conversation-plans). |
| `expected_trajectory` | Yes | Ordered list of tool names the agent is expected to call. Used by the `trajectory_matcher` scorer. See [Tool name format](#tool-name-format). |
| `max_turns` | Yes | Maximum number of conversation turns before the evaluation stops. |
| `env` | Optional | Per-scenario environment variables, merged with the model config's `env`. |
| `kind` | Optional | Category label for grouping and reporting (e.g. `"tools"`). |
| `work_dir` | Optional | Working directory the agent runs in. Relative paths resolve against the evalset file's own directory, and the directory is created if it doesn't exist. Use this for scenarios where the agent must read or write project files. |
| `binary_rubric` | Optional | Array of pass/fail criteria evaluated by the `binary_rubric_scorer`. Each criterion is scored independently and reports as `binary_rubric_scorer_<index>`. |
| `expected_skills` | Optional | List of skill names the agent is expected to activate. Used by the `skills_trajectory` scorer. |

## Important Notes

- **Environment placeholders:** `${VAR}` placeholders anywhere in the evalset are expanded from the environment at load time, so values like a GCP project need not be hard-coded. An unresolved placeholder **fails the run immediately** rather than passing a literal `${VAR}` to the agent — export the variable first.
- **Scenario filtering:** You do not need separate evalset files to run a subset. Set `scenarios` (explicit IDs) or `scenario_pattern` (a glob over IDs) in the run config to filter at load time.
- **Empty trajectories are valid:** For scenarios scored purely by rubric or goal completion, set `"expected_trajectory": []`. The `trajectory_matcher` scorer can simply be left out of the run config.

## Tool name format

Entries in `expected_trajectory` use the canonical form `<server>__<tool>` (double-underscore separator) for MCP tools, and the bare name for native harness tools (e.g. `Read`, `Bash`, `run_shell_command`).

Each harness adapter normalizes its raw tool-call events into this form at the boundary, so the same evalset scores runs from Gemini CLI, Claude Code, Codex CLI, and Antigravity without modification. The `<server>` segment comes from the MCP server key in your model config and is **case-sensitive** — e.g. `cloud-sql` or `bigtable`. See [`evalbench/generators/models/tool_naming.py`](/evalbench/generators/models/tool_naming.py) for the canonicalization helper.

By default `trajectory_matcher` drops native/harness-internal tools — anything not in canonical `<server>__<tool>` form — from both the expected and actual lists before scoring. This lets authors keep `expected_trajectory` focused on user-visible MCP intent without the score being dragged down by harness-internal calls like `Bash` or `update_topic`. Set `filter_native_tools: false` on the scorer to score native tool usage too.

## Writing Good Conversation Plans

The `conversation_plan` instructs the simulated user LLM how to behave, and it shapes the evaluation as much as the starting prompt does.

1. **Be specific about the goal.** State clearly what the user wants to accomplish.
2. **Provide concrete values.** Include the exact names, values, and parameters the simulated user should supply when asked.
3. **Handle ambiguity intentionally.** Some scenarios should test the agent's handling of vague requests (e.g. `"I need a database."`).
4. **Include decision points.** Tell the simulated user how to respond to agent confirmations or questions.
5. **Define the project context.** Specify the GCP project and any other environment details the agent will need.

## Example Entry

An ambiguous multi-turn scenario that tests whether the agent asks the right clarifying questions before acting:

```json
{
  "id": "csql-create-ambiguous-multiturn-01",
  "starting_prompt": "I need a database.",
  "conversation_plan": "The user starts with a vague request. You want to CREATE a NEW Cloud SQL instance named 'my-pg-app'. If the agent offers to create one, say YES. When asked for details, provide 'my-pg-app' as the instance name and 'user_data' as the database name. Never claim to have an existing instance. The goal is for the agent to eventually create the database 'user_data' inside 'my-pg-app' in the astana-evaluation project.",
  "expected_trajectory": [
    "cloud-sql__list_instances",
    "cloud-sql__create_instance",
    "cloud-sql__create_database"
  ],
  "env": {
    "GOOGLE_CLOUD_PROJECT": "astana-evaluation"
  },
  "kind": "tools",
  "max_turns": 6
}
```

A rubric-scored scenario with no expected tool trajectory:

```json
{
  "id": "dbt-minimal-project-01",
  "starting_prompt": "Set up a bare minimum dbt project with a single model that selects 1.",
  "conversation_plan": "The agent should set up a dbt project and run compile and run successfully.",
  "expected_trajectory": [],
  "binary_rubric": [
    "A dbt_project.yml file was created",
    "dbt compile completed without errors"
  ],
  "work_dir": "./workspaces/dbt-minimal",
  "max_turns": 5
}
```

See the [scorer reference](/docs/scorers.md#agentic-scorers) for what each scorer does with these fields.
