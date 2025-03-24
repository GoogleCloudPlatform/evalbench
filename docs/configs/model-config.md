# NL2SQL Model Configuration

This YAML configuration file defines the parameters for the generation model used in the NL2SQL project. **This configuration is completely configurable to whatever the generator needs.** Depending on the generator you choose, additional keys may be required. For example, the keys in the *GCP Specific Configuration* section are required only when using generators such as `gcp_vertex_claude` or `gcp_vertex_gemini`.

---

## General Generator Configuration

These settings are passed to all generators, regardless of the specific engine used.

| **Key**            | **Required** | **Default Value** | **Description**                                                                                                                                                                    |
| ------------------ | ------------ | ----------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `generator`        | Yes          | N/A               | Specifies the generation engine. This configuration is completely customizable based on the generator's needs.                                                                                                                                                                             |
| `base_prompt`      | Optional     | `""`              | An optional starting prompt or seed text for the model. An empty string indicates no additional prompt is provided by default.                                                                                                                                                                           |
| `max_tokens`       | Optional     | N/A               | Specifies the maximum number of tokens the model can generate in a single output.                                                                                                                                                                            |
| `execs_per_minute` | Optional     | `60`              | Sets the maximum number of executions allowed per minute. If not provided, it defaults to `60`. This helps throttle the rate of query generation.                                                                                                                                                                        |
| `max_attempts`     | Optional     | `3`               | Specifies the maximum number of attempts for query generation in case of failures. Defaults to `3` if not provided.                                                                                                                                                                          |

---

## GCP Specific Configuration

These settings are **required only** for generators that utilize Google Cloud Vertex AI, such as `gcp_vertex_claude` and `gcp_vertex_gemini`. They are not needed for other generators.

| **Key**           | **Required for**                           | **Default Value**              | **Description**                                                                                                                                                                           |
| ----------------- | ------------------------------------------ | ------------------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `gcp_project_id`  | `gcp_vertex_claude`, `gcp_vertex_gemini`   | ''                             | The Google Cloud Project ID that hosts your Vertex AI resources.                                                                                                                                                                                |
| `gcp_region`      | `gcp_vertex_claude`, `gcp_vertex_gemini`   | ''                             | The Google Cloud region where the Vertex AI service is deployed.                                                                                                                                                                                 |
| `vertex_model`    | `gcp_vertex_claude`, `gcp_vertex_gemini`   | ''  | The identifier for the specific model deployed on Vertex AI, including version or timestamp details.                                                                                                                                                                                  |

---

## Important Notes

- **Customization:** This configuration is fully customizable to the needs of the selected generator. You can add or remove keys as necessary.
- **Generator Specifics:** When using generators like `gcp_vertex_claude` or `gcp_vertex_gemini`, ensure that the GCP specific settings are provided. For other generators, these keys may be omitted.
- **Prompt Customization:** Use `base_prompt` to provide initial context or instructions to the model if needed; otherwise, it can remain empty.
- **Token Limits:** Adjust `max_tokens` based on the complexity and expected length of responses. A higher token limit allows for more detailed output but may also increase computational costs.
- **Rate Limiting & Retries:** The `execs_per_minute` and `max_attempts` keys help control the query generation process, ensuring that you can stay below project quota limits.

---

## Example Configuration

Below is an example of the updated YAML configuration file:

```yaml
# General Generator Configuration
generator: gcp_vertex_gemini
base_prompt: ""
max_tokens: 1024
execs_per_minute: 60
max_attempts: 3

# GCP Specific Configuration (Required for gcp_vertex_claude and gcp_vertex_gemini)
gcp_project_id: my_cool_gcp_project
gcp_region: us-east5
vertex_model: gemini-2.0-pro-exp-02-05
```
