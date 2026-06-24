# Data Cloud MCP Tool Style Guide

[TOC]

*   **Go link:**
    [go/mcp-datacloud-styleguide](http://go/mcp-datacloud-styleguide)

## Style Principles

This document provides style guidelines and best practices for Data Cloud teams
building MCP (Model Context Protocol) tools.

> [!NOTE] The tool descriptions and examples in this guide are generated using
> the `experimental/users/kvg/mcp_readability` utility. You can use this tool to
> view the API of your own MCP tools in a readable format by running it against
> your MCP server endpoint:
>
> ```bash
> blaze run //experimental/users/kvg/mcp_readability:mcp_list_tools -- \
>     --url="https://your-mcp-server.googleapis.com/mcp" \
>     --auth \
>     --project="your-gcp-project"
> ```
>
> You can use the following tool to get LLM generated recommendations (AI
> evaluation) based on this style guide for your MCP tools by running it against
> your MCP server endpoint. This tool requires a Gemini API key to run the AI
> evaluation:
>
> ```bash
> blaze run cloud/databases/mcp/readability:run_mcp_readability_check -- \
>     --gemini_api_key <gemini_api_key> check_url \
>     --url "https://your-mcp-server.googleapis.com/mcp" \
>     [--tools <tool_names>] [--auth] [--project <project_id>]
> ```

There are a few overarching principles that summarize how to think about
building good MCP tools. The following are attributes of good tools, in order of
importance:

1.  **Clarity**: The tool's purpose, parameters, and error messages are clear to
    the reasoning engine.
2.  **Simplicity**: Tools and their parameters are simple enough to reduce
    cognitive load on the model, minimize hallucination risks, and avoid
    excessive token usage, while remaining comprehensive enough to complete a
    task.
3.  **Security**: Tools are secure by default, do not expose secrets, and use
    granular protections.
4.  **Maintainability**: Tools are built in a consistent manner, relying on
    standardized platforms, making them easier to maintain across teams.

## Platform & Constraints

### Platform to build tools <!-- priority: p0 -->

All teams within data cloud **MUST** be using either OneMCP or MCP Toolbox for
tool building. Bespoke solutions are **NOT** supported.

### Tool Count Limits <!-- priority: p0 -->

The current rule of thumb is to keep it to 40 tools per server as an upper
limit, though even this amount may cause performance issues. Performance
degrades as more tools are added, so teams **SHOULD** heavily weigh adding new
tools against the negative impact on tool accuracy until other mechanisms are in
place to deal with this.

In the future, we aspire for this number to be reduced to 5-8 tools per server
as toolsets/skills capabilities improve in MCP Toolbox and OneMCP, though this
is not a requirement today. Until overall platform tool handling improves, teams
**SHOULD** minimize tool count as much as possible.

## Tool Design & API

MCP tools **MUST** be high quality, reliable, and easy for agents to use.

### Tool Names <!-- priority: p1 for <action>_<resource>, p2 for snake_case -->

Teams **MUST** use `snake_case` (`<action>_<resource>`) for tool names. You
**SHOULD NOT** use product-specific prefixes, as the agent can automatically
disambiguate by prefixing the tool name with the MCP server name.

```text {.bad}
================================================================================
TOOL: my_service_create_instance
================================================================================
================================================================================
TOOL: my_service_execute_sql
================================================================================
```

```text {.good}
================================================================================
TOOL: create_instance
================================================================================
================================================================================
TOOL: execute_sql
================================================================================
```

All new tools **MUST** be registered in the
[Data Cloud OneMCP: Tools and Commitment Dashboard](https://docs.google.com/spreadsheets/d/1rXWhXONd5xqCpU-oJJ8eTSWtL4QVm9yuavjur-p_NXY){.external}.

### Keep tools CUJ-focused <!-- priority: p0 -->

Tools **SHOULD** be focused on a specific CUJ or goal, like creating a resource
or fetching monitoring data. Aim for tools that are granular enough to be easily
understood and used by agents, but comprehensive enough to complete a task in
one go if possible. For example, if a CUJ requires multiple API calls, consider
creating one tool that orchestrates these calls to reduce chances of multi-tool
failure.

However, avoid bundling unrelated actions into a single tool.

```text {.bad}
================================================================================
TOOL: manage_instance
================================================================================
DESCRIPTION:
  Creates, deletes, or retrieves a database instance depending on the action
  parameter.

PARAMETERS:
  action (string) [REQUIRED]
      Required. The action to perform (create, delete, get).

  name (string) [REQUIRED]
      Required. Name of the database instance. This does not include the
      project ID.
```

```text {.good}
================================================================================
TOOL: get_instance
================================================================================
DESCRIPTION:
  Get the details of a Cloud SQL instance.

PARAMETERS:
  instance (string) [REQUIRED]
      Required. Database instance ID. This does not include the project_id.

  project_id (string) [REQUIRED]
      Required. Project ID of the project that contains the instance.
```

### Idempotency <!-- priority: p1 -->

Whenever possible, tools **SHOULD** be idempotent.

**Why?** Calling the same tool with the same parameters multiple times should
have the same result as calling it once. This is especially important for create
and delete operations. For example, if an agent calls `create_resource` multiple
times with the same parameters, only one resource should be created. If the
resource already exists, the tool should return a success status code rather
than an error code (e.g. `ALREADY_EXISTS`). Returning success on successive
attempts allows agents to safely retry operations without causing tool execution
to fail. It is acceptable for the tool response to indicate that the resource
already existed rather than being created by the current call.

### Parameters

Parameters **MUST** have clear and meaningful names. Avoid ambiguity that could
lead to accidental insecure configurations or accidental destructive operations.

#### Naming Convention <!-- priority: p2 -->

Teams should use `snake_case` for all parameter names. This aligns with Python
naming conventions (the most-used language for MCP servers) and provides a
predictable, standardized interface for the reasoning engine.

```text {.bad}
PARAMETERS:
  projectId (string) [REQUIRED]
      Required. Project ID of the project.
```
```text {.good}
PARAMETERS:
  project_id (string) [REQUIRED]
      Required. Project ID of the project.
```

#### Understandability <!-- priority: p0 -->

A good rule of thumb is: if a human cannot easily understand a parameter without
reading the documentation, the name is likely too ambiguous for a model as well.

Directly mapping to existing enums or field names from underlying OnePlatform
APIs is often an antipattern. These internal names are typically designed for
system-to-system communication and backwards compatibility, not for semantic
understanding by an LLM.

```text {.bad}
PARAMETERS:
  write_disposition (string) [OPTIONAL]
      Optional. Specifies the action that occurs if the destination table
      already exists. Can be ['WRITE_TRUNCATE', 'WRITE_APPEND', 'WRITE_EMPTY'].
```

```text {.good}
PARAMETERS:
  if_table_exists (string) [OPTIONAL]
      Optional. Action to take if the destination table already exists.
      Can be ['overwrite', 'append', 'fail'].
```

**Explain Domain Terminology** Do not assume the agent understands your
specific product's architecture or internal naming conventions. If a tool relies
on domain-specific concepts (e.g., "Exadata," "Static-IP Connectivity,"
"Statement Suggestions"), you MUST provide a brief, plain-English explanation of
what the concept is and *why* the agent would use it.

To prevent tool descriptions from becoming excessively long, keep these
explanations concise.

Create evaluations
([evalbench](http://g3doc/cloud/databases/mcp/g3doc/evalbench_overview.md))
to understand how well an agent handles domain terminology, and to test what
level of explanations you need to include in the tool description.

#### Query Languages and Custom Filters <!-- priority: p0 -->

If a tool takes a custom query string as an input parameter (e.g., AIP-160,
PromQL, EBNF syntax):

1.  **Provide Concrete Examples**: You MUST include 2-3 fully-formed examples of
    valid query strings in the parameter description.

2.  **Explain Literals**: If the filter requires specific internal string
    literals, list the most common valid literals directly in the description.

3.  **Iterative Discovery**: If the domain is too large to document in the
    description (e.g., thousands of PromQL metrics), you SHOULD provide a
    secondary "discovery" tool(e.g., `list_available_metrics`) so the agent can
    look up the correct literals before composing its filter.

Evaluating agent performance on query generation is crucial for understanding
how much detail and how many examples are necessary to include in tool
descriptions. You SHOULD use evaluations to test how well agents handle query
languages and custom filters with varying levels of detail in the description.

*   *Note*: A fuller explanation of complex query languages might be better
    suited as a separate skill.

**Examples:**

```text {.good}
PARAMETERS:
  filter (string) [OPTIONAL]
      Optional. An AIP-160 filter string. Examples:
      - 'labels.env = "prod" AND labels.team = "data"'
      - 'create_time > "2024-01-01T00:00:00Z"'
```

```text {.good}
PARAMETERS:
  query (string) [REQUIRED]
      Required. A PromQL query. Examples:
      - 'up{job="kubernetes-pods"}'
      - 'sum(rate(http_requests_total[5m]))'
```

#### Avoid complex parameters <!-- priority: p0 -->

Avoid complex parameter types like large, nested protocol buffers or overly
complex dictionaries. These increase the likelihood of parsing errors, consume
unnecessary tokens, and cause model confusion. Stick to simple primitives
(strings, integers, booleans) wherever possible. Simple maps (e.g., for labels
or simple key-value configurations) are acceptable if they are well-described
and do not introduce large, nested proto structures.

```text {.good}
// GOOD: Simple maps for flat key-value data
PARAMETERS:
  labels (object) [OPTIONAL]
      Optional. Labels to apply to the instance. A simple map of string keys
      to string values (e.g., {"env": "prod", "team": "data"}).
```

**Opinionated Defaults for Resource Creation** When wrapping `Create` endpoints
for complex resources, DO NOT expose the entire OnePlatform proto schema as
tool parameters. Instead, provide sensible and secure defaults for the
resource configuration on the server side, and only expose parameters for
the 5 to 8 most critical settings the agent needs to configure
(e.g., `machine_type`, `region`, `memory_gb`). If providing sensible defaults
is not sufficient to reduce complexity, you **SHOULD** split functionality into
separate, purpose-built MCP tools rather than creating one complex tool.

```text {.bad}
// BAD: Exposing a massive, complex proto object
PARAMETERS:
  cluster_config (object) [REQUIRED]
      Required. The complete cluster configuration proto, including nested
      fields for master_config, worker_config, software_config, etc.
```

```text {.good}
// GOOD: Opinionated defaults and simple parameters
PARAMETERS:
  cluster_name (string) [REQUIRED]
      Required. The name of the cluster.

  node_count (integer) [OPTIONAL]
      Optional. The number of worker nodes. Defaults to 3.

  machine_type (string) [OPTIONAL]
      Optional. The machine type to use for nodes. Defaults to 'n1-standard-4'.
```

**Restructuring Complex Protos** Instead of passing a
complex `Settings` proto for instance creation, flatten the critical
fields:

```text {.bad}
PARAMETERS:
  settings (object) [OPTIONAL]
      Optional. Instance settings proto containing tier, pricingPlan,
      ipConfiguration (which itself contains authorizedNetworks, ipv4Enabled, etc).
```

```text {.good}
PARAMETERS:
  tier (string) [REQUIRED]
      Required. The service tier (e.g., 'db-custom-1-3840').

  enable_public_ip (boolean) [OPTIONAL]
      Optional. Whether to enable public IP. Defaults to false.

  authorized_networks (array) [OPTIONAL]
      Optional. List of authorized CIDR blocks.
```

#### Hide Useless and Deprecated Fields <!-- priority: p1 -->

You MUST NOT expose parameters to the agent that it cannot meaningfully use.
Leaking backend or OnePlatform requirements into the AI surface increases
cognitive load and token waste.

-   **Idempotency Tokens & UUIDs**: Do not ask the agent to generate UUIDs or
    `request_id`s for idempotency. Handle idempotency and request deduplication
    inside the MCP server or adapter layer. These should NOT be included in the
    input payload schema.
-   **Deprecated Fields**: Strip all deprecated fields from your schemas.

#### Use Human-Readable Time and Durations <!-- priority: p0 -->

When designing parameters for dates, times, or durations, you **MUST NOT** use
low-level Unix timestamps (e.g., milliseconds or nanoseconds). Instead, use
higher-order time units (seconds, minutes, hours, days) or standard
human-readable date-time formats.

LLMs reason much better with formatted dates, times, and higher-order durations
than they do with raw Unix timestamps. Forcing the reasoning engine to calculate
nanoseconds increases the likelihood of hallucination and token waste.

```text {.bad}
PARAMETERS:
  start_time_ns (integer) [OPTIONAL]
      Optional. The start time of the query in nanoseconds since the Unix epoch.

  timeout_ms (integer) [OPTIONAL]
      Optional. The timeout duration for the job in milliseconds.
```
```text {.good}
PARAMETERS:
  start_time (string) [OPTIONAL]
      Optional. The start time of the query in RFC 3339 format
      (e.g., "2024-04-09T22:52:14Z").

  timeout_in_minutes (integer) [OPTIONAL]
      Optional. The timeout duration for the job in minutes.
```

#### Limit Options <!-- priority: p1 -->

Aim for a limit of 5 to 8 parameters per tool, while keeping in mind that fewer
parameters is usually better. High parameter counts exponentially decreases tool
call accuracy and can increase token consumption.

#### Use Enums <!-- priority: p1 -->

Prefer iterating a list of possible values over free-text strings. This
constrains the model's decision path and reduces token perplexity.

```text {.good}
status: ['open', 'closed']
```

#### Safe Pagination <!-- priority: p0 -->

If your tool returns lists of values (for example resources): -
**Cap Page Sizes**: Default `page_size` parameters MUST be capped at an LLM-safe
limit (e.g., 10 to 20 maximum). Aim to keep the return output size
below 5,000 to 10,000 tokens to avoid using up too much of an agent's context
window.

-   **Document Page Tokens**: If exposing a `page_token` parameter, explain
    exactly how the agent should use it (e.g., *"Pass the `next_page_token`
    received from the previous response to retrieve the next page."*).

#### Concise Descriptions <!-- priority: p0 -->

While it can be tempting to treat tools like documentation, avoid adding
redundant information to tool descriptions.

-   **Keep Parameter Docs on the Parameters**: Do not repeat information in the
    tool description that belongs in parameter descriptions. For example, do not
    embed parameter definitions, formatting rules, or extraneous section titles
    (such as "Purpose and Capabilities") within the main tool description. Each
    parameter has its own dedicated description field in the schema for this
    information. Stating "The request must provide parameter X to get result Y"
    in the main description is redundant and wastes tokens.

```text {.good}
================================================================================
TOOL: execute_sql
================================================================================
DESCRIPTION:
  Execute any valid SQL statement, including data definition language (DDL),
  data control language (DCL), data query language (DQL), or
  data manipulation language (DML) statements, on a Cloud SQL instance.

PARAMETERS:
  database (string) [OPTIONAL]
      Optional. Name of the database on which the statement will be executed.
      For Postgres it's required, for MySQL it's optional. For Postgres, if your
      query is not scoped to an existings database, like list databases / create
      new database / grant roles, you can pass in default value as postgres.

  instance (string) [REQUIRED]
      Required. Database instance ID. This does not include the project_id.

  project_id (string) [REQUIRED]
      Required. Project ID of the project that contains the instance.

  sql (string) [REQUIRED]
      Required. SQL statements to run on the database. It can be a single
      statement or a sequence of statements separated by semicolons.
```

#### Avoid Conversational Scripting <!-- priority: p0 -->

-   **Use Imperative Phrasing**: You may use imperative phrasing to guide agent
    actions, including asking for user confirmation on critical or destructive
    operations (e.g., *"Please explicitly check with the user to ensure they
    want to delete all data before executing"*). However, do not overly
    prescribe conversational flow (e.g., *"Send each step as a separate
    message"*).
-   **Do not reference capabilities an agent may not have**: For example, do not
    tell the agent to *"sleep for 10 seconds"* or *"navigate to this URL in a
    browser"*. While some agents can have these capabilities, asking an agent
    who does not may confuse or derail them. Use objective constraints instead
    (e.g., *"Wait at least 10 seconds before polling again"*). Tool
    descriptions should explain what the tool does, and they should avoid
    telling the agent to do things it cannot or to hide things that require
    user interaction.

#### Be Consistent <!-- priority: p0 -->

Use consistent parameter names for the same concept across different tools.
Decide on one parameter name for project identification (e.g., `project_id`) and
use it across all tools.

```text {.bad}
PARAMETERS:
  project (string) [REQUIRED]
      Required. ID of the project.

# ...
PARAMETERS:
  project (string) [REQUIRED]
      Required. Name of the project.

```

```text {.good}
PARAMETERS:
  instance_id (string) [REQUIRED]
      Required. Database instance ID.

  project_id (string) [REQUIRED]
      Required. Project ID of the project that contains the instance.
```

#### Explicit Consequences <!-- priority: p1 -->

Explicitly state consequences in parameter names. For destructive or high-cost
operations, parameter names should make consequences clear.

```text {.bad}
  force (boolean) [OPTIONAL]
      Optional. Force the deletion.
```

```text {.good}
PARAMETERS:
  confirm_deletion (boolean) [OPTIONAL]
      Optional. Acknowledge that this operation will permanently delete all
      clusters and data. This action cannot be undone.
```

#### Avoid Templated Strings <!-- priority: p0 -->

**DO NOT** require the agent to pass parameters in as templated strings (e.g.,
`projects/{project_id}/locations/{region}/instances/{resource_name}`).

This creates an additional formatting step for the agent to construct the string
correctly, which increases the likelihood of hallucination or syntax errors and
potentially lowers the tool's success rate.

#### Parameter Design Examples

Tool              | Bad Design (Ambiguous/Insecure) | Good Design (Explicit/Secure)
:---------------- | :------------------------------ | :----------------------------
`deploy_dag`      | `force: true`                   | `acknowledge_overwrite_of_existing_dag_file: true`
`cancel_job`      | `immediate: true`               | `termination_policy: "FORCE_CANCEL_WITHOUT_DRAIN"`
`instance_delete` | `confirm: true`                 | `confirm_permanent_deletion_of_all_clusters_and_data: true`
`Metadata Lookup` | `show_all: true`                | `include_sensitive_business_metadata: true`
`batch_create`    | `use_public_ip: true`           | `network_access: "PRIVATE_ONLY"` or `"PUBLIC_INTERNET_ACCESS"`
`database_delete` | `delete: true`                  | `acknowledge_permanent_database_deletion_and_data_loss: true`
`execute_sql`     | `allow_large_results: true`     | `acknowledge_potential_high_compute_cost_for_large_results: true`
`duration`        | `duration: "4h"`                | `requested_access_duration_in_hours: 4`
`justification`   | `reason: "fixing bug"`          | `detailed_justification_for_elevated_privileges: "b/1234 -- Fixing bug"`

### Long running operations <!-- priority: p1 -->

MCP today does not have a standard for how to do long running operations. We
expect that our tools will have to evolve as the MCP standard changes.

In the meantime, the standard pattern for handling LROs is:

1.  **Initiation**: An MCP tool call that initiates an LRO (e.g.,
    `create_instance`) **MUST** return immediately with an operation ID.
2.  **Polling**: The agent will use a dedicated tool, like `get_operation`, to
    poll the status of the LRO using the returned operation ID. Teams **MUST**
    provide such a polling tool.
3.  **Completion**: The agent will continue polling the tool until the operation
    reaches a terminal state (e.g., `SUCCEEDED`, `FAILED`).

To ensure agents can effectively follow this pattern, clear descriptions and
hints **MUST** be provided within the tool descriptions. This guides the agent
on the expected workflow.

Example tool descriptions:

```text {.bad}
================================================================================
TOOL: create_instance
================================================================================
DESCRIPTION:
  Creates a database instance.
```

```text {.good}
================================================================================
TOOL: create_instance
================================================================================
DESCRIPTION:
  Initiates the creation of a Cloud SQL instance.

  * The tool returns a long-running operation. Use the `get_operation` tool to
  poll its status until the operation completes.
  * The instance creation operation can take several minutes. Wait at least
  30 seconds before rechecking the status.

PARAMETERS:
  name (string) [REQUIRED]
      Required. Name of the Cloud SQL instance. This does not include the
      project ID.

  project (string) [REQUIRED]
      Required. Project ID of the project to which the newly created Cloud SQL
      instances should belong.

================================================================================
TOOL: get_operation
================================================================================
DESCRIPTION:
  Retrieves the status of a long-running operation.
```

## Error Handling

### Actionable Error Messaging <!-- priority: p0 -->

Tools **MUST** return clear and actionable error messages that tell agents what
went wrong, why it went wrong, and how to fix it. When an operation fails, **DO
NOT** return generic error messages like "internal error".

For more details on writing good error messages, see
[Write actionable error messages](https://developers.google.com/workspace/chat/write-error-messages).

Here are some examples of good and bad error messages for tools:

| Tool              | Bad error message   | Good error message                |
| :---------------- | :------------------ | :-------------------------------- |
| `create_instance` | `Invalid request`   | `Failed to create instance: The   |
:                   :                     : instance name 'my-instance' is    :
:                   :                     : already in use. Please choose a   :
:                   :                     : different name.`                  :
| `execute_sql`     | `Permission denied` | `Failed to execute SQL: The       |
:                   :                     : service account does not have     :
:                   :                     : permission to access the database :
:                   :                     : 'my-db'. Please grant 'Cloud SQL  :
:                   :                     : Client' role to the service       :
:                   :                     : account.`                         :
| `delete_resource` | `Internal error`    | `Failed to delete resource: The   |
:                   :                     : resource is currently in use by   :
:                   :                     : another operation. Please try     :
:                   :                     : again in a few minutes.`          :

## Security Best Practices

This section covers security best practices for creating MCP tools. Authoring a
new MCP tool is an opportunity to make tools that are secure by default.

### Secure by Default <!-- priority: p0 -->

Our APIs **MUST** enforce secure configurations.

*   **Critical Requirement:** All "create" or "update" operations **MUST** apply
    the most secure configuration by default. For example, any resource that
    *can* be encrypted *must* be encrypted by default with Google-managed or
    customer-managed encryption keys. Any resource that *can* be exposed to the
    public internet *must* default to private. If defaulting to private creates
    significant user friction (e.g., forcing VPC setup) or represents an
    irreversible configuration, network configuration may be defined as a
    required parameter, forcing the agent to request clarification if the user's
    intent is unclear.

### Tool Descriptions as Embedded Security Guidance <!-- priority: p0 -->

The description field in a tool's manifest is the primary documentation for an
agent and **MUST** guide the reasoning engine toward correct actions. **DO NOT**
use field descriptions like "enable_policy" with a boolean value.

| Field                 | Bad Description   | Better Description               |
| :-------------------- | :---------------- | :------------------------------- |
| `enable_public_ip`    | Enables public IP | Enabling this flag makes the     |
:                       :                   : resource accessible from the     :
:                       :                   : public internet, which is a      :
:                       :                   : security risk if not properly    :
:                       :                   : managed. It is recommended to    :
:                       :                   : keep resources private whenever  :
:                       :                   : possible.                        :
| `deletion_protection` | Enables deletion  | Prevents the resource from being |
:                       : protection        : accidentally deleted.            :
:                       :                   : Recommended for production       :
:                       :                   : resources to prevent human       :
:                       :                   : error.                           :

### Handling Passwords and Secrets <!-- priority: p0 -->

Tools **MUST NOT** surface passwords or credentials in clear-text requests or
responses. Authentication **MUST** be handled by the underlying transport (e.g.,
service account credentials). Exposing plaintext password fields in API
interfaces for AI consumption is a security risk.

*   **Policy:**
    [Read More](https://docs.google.com/document/d/1D6FGIJTfDlNTwPE00W0jStP6zB4Gbdyy0QSOnewKbPo)
    {.external}
*   Teams may allow agents to access references to secrets stored in Secret
    Manager

### Granular Protection for Sensitive Operations <!-- priority: p0 -->

Many GCP APIs follow a pattern of bundling multiple update operations into a
single API method (e.g., `resource.update`), controlled by a single IAM
permission (e.g., `my-service.resources.update`). While efficient, this can
expose users to extra risk when authorizing AI agents. For example, a user might
want to grant an agent permission to update resource labels, but not to enable
public internet access for the resource, which is a high-risk action. If both
actions are covered by `my-service.resources.update`, the user cannot easily
grant permission for one but not the other. This prevents users from
establishing safe boundaries for their agents.

To mitigate this, teams **SHOULD** consider introducing granular protections for
high-risk actions such as deleting data or exposing a resource to the public
internet. For example:

*   **Field-level IAM permissions:** For API methods that update multiple
    fields, consider requiring more specific IAM permissions for changes to
    high-risk fields. For example, enabling public internet access via a
    `resource.update` method could require
    `my-service.resources.enablePublicAccess` in addition to
    `my-service.resources.update`.
*   **Multi-party Approval:** For critical operations like deleting a production
    resource, consider requiring a second independent confirmation from another
    user before execution.

### Horizontals and Compliance <!-- priority: p0 -->

All MCP tools **MUST** comply with standard Google Cloud horizontal requirements
including the standard ones:

*   **VPC-SC:** Integration with VPC Service Controls.
*   **IAM:** Use Cloud IAM for authorization and support IAM Deny policies.
*   **Audit Logging:** Working `Gin` logging for all tools.
*   **Access Transparency (AXT/AXA):** Integration for administrative access
    logs.
