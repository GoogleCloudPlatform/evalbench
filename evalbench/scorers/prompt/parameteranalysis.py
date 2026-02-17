PARAMETER_ANALYSIS_PROMPT = """\
You are an expert evaluator assessing the tool parameters supplied by an AI agent over the course of a conversation.
Focus on identifying any issues with parameter construction and suggesting improvements to tool descriptions or requirements.

### Input Data
**Conversation History:** {conversation_history}
**Tools Called:** {accumulated_tools}

### Best Practices for Tool Design
When providing your suggestions, consider the following best practices for tool building:
- **Tool Names**: Use `snake_case` (`<action>_<resource>`) and avoid product-specific prefixes.
- **Focus**: Tools should be focused on a specific task. Aim for tools comprehensive enough to complete a task in one go, but avoid bundling unrelated actions.
- **Idempotency**: Whenever possible, tools should be idempotent (e.g., returning success if a resource to be created already exists).
- **Actionable Error Messages**: Errors should be clear and actionable, explaining what went wrong, why, and how to fix it instead of generic errors.
- **API Clarity**:
  - Simplicity First: Stick to simple primitives (strings, integers, booleans), avoiding complex nested JSON, maps, or protos.
  - Limit Options: Aim for <5 parameters per tool.
  - Use Enums: Prefer enums over free-text strings or booleans for mutually exclusive options to constrain choices.
  - Concise Descriptions: Provide clear descriptions but avoid extreme detail not relevant to all use cases.
  - Be Consistent: Use consistent parameter names across tools (e.g., always `project_id` rather than mixing `project` and `project_name`).
  - Explicit Consequences: For destructive or high-cost operations, parameter names should explicitly state consequences (e.g., `acknowledge_overwrite_of_existing_dag_file: true` rather than `force: true`).
- **Security Best Practices**:
  - Secure by Default: Create or update operations must apply the most secure configuration by default (e.g., private network access). Tool descriptions must guide the agent on the security implications of overriding defaults.
  - Passwords and Secrets: Tools MUST NOT surface passwords or credentials in clear-text requests or responses.
  - Granular Protection: High-risk actions (e.g., exposing to public internet) should ideally have specific protections or explicit parameters.

### Task
Analyze the parameters provided to the tools during this interaction. Evaluate whether the agent was able to construct them correctly, or whether it struggled. Provide suggestions on how the tool descriptions could be rewritten or improved to help the agent supply better parameters or adhere more closely to best practices in the future.

### Output Format
Provide your response in the following format:
Analysis: <Detailed critique of the parameters provided by the agent>
Suggestions: <Any recommended changes to the tool descriptions, schemas, or naming conventions based on the analysis and best practices>
"""
