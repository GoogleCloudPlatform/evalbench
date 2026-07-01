# Data Cloud MCP Tool Style Guide (sample)

This is a small, illustrative style guide for the MCP readability check. Each rule
heading is annotated with a priority (`<!-- priority: pX -->`) and a stable
`{#tag}` anchor. The readability judge uses the priority to assign severity
(`p0`→P0, `p1`→P1, `p2`→P2) and uses the `{#tag}` anchor as the `rule_id`, so
waivers in `exceptions.yaml` stay valid even when a heading is reworded.

### Tool Names <!-- priority: p1 --> {#tool-names}

Tool names should be `snake_case` and read as `<action>_<resource>` (e.g.
`list_datasets`, `get_instance`). Avoid abbreviations or internal jargon an agent
is unlikely to recognize.

### Concise, Complete Descriptions <!-- priority: p1 --> {#descriptions}

Every tool and every parameter must have a description that says what it does and
when to use it. Descriptions should be concise but self-contained — the agent
should not need external documentation to call the tool correctly.

### Use Enums for Closed Sets <!-- priority: p2 --> {#use-enums}

When a parameter accepts a fixed set of values, declare them as an `enum` in the
input schema rather than describing the options in prose, so the agent always
picks a valid value.

### Safe Pagination <!-- priority: p0 --> {#safe-pagination}

List operations that can return large result sets must expose pagination (for
example `page_size` and `page_token`) so an agent can page through results safely
instead of requesting an unbounded response.
