# MCP Tool Surface Compliance Guide

This guide is what the LLM judge consults when grading an MCP server's
tool surface. Each rule is tagged with a severity (`P0` / `P1` / `P2`). The
judge is instructed to emit findings using only these severities — and to
not invent new ones.

---

## Severity definitions

- **P0 — Functional defect or correctness risk.** The agent will get the
  wrong answer, miss data, exhaust context, or be unable to recover from a
  failure mode. Must fix before shipping.
- **P1 — Reliability / safety / discoverability gap.** Agent will work in
  the happy path but will be slow, expensive, or hard to use safely
  (destructive operations without acknowledgement; tool descriptions that
  contradict the schema; missing error semantics). Should fix soon.
- **P2 — Style / convention drift.** Works correctly but violates a naming
  or formatting convention that hurts predictability or readability. Fix
  when convenient.

---

## Rules

### R1. Safe pagination for list operations — P0

Any tool that returns a collection (`list_*`, `search_*`, `get_*_history`)
must accept `page_size` (with a sane default ≤ 50) and `page_token` /
`next_page_token`. Returning an unbounded list risks blowing the agent's
context window and wasting tokens. Tools that omit these parameters get a
P0 finding.

### R2. Parameter name ↔ description consistency — P0

Every parameter referenced in a tool description must use the **exact same
name** that appears in the schema (e.g. if the schema declares `projectId`,
the description must say `projectId`, not `project_id`). Mismatches confuse
the agent and lead to broken tool calls.

### R3. Explicit acknowledgement for destructive operations — P1

Any tool that can mutate or destroy data without easy recovery (`DROP`,
`DELETE`, `TRUNCATE`, force-recreate, etc.) must require a typed boolean
parameter such as `acknowledge_potential_data_loss` or
`confirm_destructive_query`. Tools that perform destructive work silently
get a P1 finding.

### R4. Tool naming — `<action>_<resource>` — P1

Tool names should follow `<action>_<resource>` in snake_case
(`list_instances`, `create_user`, `delete_database`). Names that read
backwards (`instances_list`), use camelCase (`listInstances`), or are
opaque (`do_it`, `query`) get a P1 finding.

### R5. snake_case parameter names — P2

Parameters should be snake_case (`project_id`, `instance_name`,
`dry_run`), not camelCase. The reasoning engine emits snake_case more
reliably across model families.

### R6. Description quality — P1

Every tool must have a description that:
- States *what* it does in one sentence.
- States *when to use it* (or what it differs from sibling tools).
- States the *side effects* (mutates state? incurs cost?).

Tools with a one-word description, or with a description that just
restates the tool name, get a P1 finding.

### R7. Constrained types over free-form strings — P2

When a parameter has a fixed value set, declare it as `enum`. When it
matches a known format (datetime, duration, region code, IPv4), declare
`format` or `pattern`. Tools that take an unconstrained `string` for
something with an obvious enum/format get a P2 finding.

### R8. Required vs optional clarity — P2

Every parameter must be marked `required: true` or `required: false`
explicitly in the schema; the description should not contradict the
required flag (e.g. saying "optional" while the schema marks it
required). Mismatches get a P2 finding.

### R9. Return shape documentation — P1

The tool description (or `returns:` block in the YAML) must say what the
agent gets back: shape, key fields, units. Tools whose response is opaque
(`returns: object`) without a description get a P1 finding.

---

## Output contract for the judge

Return JSON with exactly this top-level shape:

```json
{
  "general": {
    "P0": [{"title": "...", "description": "..."}],
    "P1": [...],
    "P2": [...]
  },
  "per_tool": {
    "<tool_name>": {
      "P0": [{"title": "...", "description": "..."}],
      "P1": [...],
      "P2": [...]
    }
  }
}
```

Rules:
- `general` is for issues that span multiple tools or the bundle as a whole
  (e.g. naming convention drift across many tools, missing pagination
  pattern across all list tools).
- `per_tool` is for issues scoped to one tool. Use the tool's exact name
  as it appears in the input bundle.
- Each finding has a short `title` (≤ 80 chars, sentence case, no trailing
  period) and a `description` (1–3 sentences, ending with a concrete
  suggestion when possible).
- Do not invent severities outside `P0` / `P1` / `P2`.
- If a tool has no issues, omit it from `per_tool` (or include with empty
  severity lists; the renderer handles both).
