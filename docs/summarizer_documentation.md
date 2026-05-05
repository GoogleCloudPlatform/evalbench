# EvalBench Summarizer Logic and Formula Rationale

## Summarizer Logic

The EvalBench summarization pipeline has been optimized to handle large numbers of evaluation runs efficiently and reliably.

### 1. Parallel Processing
To reduce latency, `viewer/precompute_trends.py` uses a `ThreadPoolExecutor` with **10 workers** to process evaluation directories in parallel. This allows multiple AI summaries to be generated concurrently.

### 2. Asynchronous Cloud Run Job
Heavy precomputation workloads have been moved off the main web service to a dedicated **Cloud Run Job** (`precompute-job`). This prevents the web service from blocking or timing out during heavy processing.

### 3. Dual Authentication Support
The summarizer supports two modes of operation depending on available credentials:
-   **API Key Mode (Preferred for Dev)**: If the `GOOGLE_API_KEY` environment variable is set, the summarizer bypasses the default Vertex AI configuration and uses the `google-genai` SDK directly to call **`gemini-2.5-flash`** via Google AI Studio. This is faster and avoids IAM permission complexities.
-   **Vertex AI Mode (Default Fallback)**: If no API key is set, it falls back to the project's default generator (`gcp_vertex_gemini`) using **`gemini-2.5-pro`** via Vertex AI, relying on Service Account IAM permissions (`roles/aiplatform.user`).

### 4. Rate Limit Resilience
To handle high concurrency without failing, the summarizer implements **exponential backoff retry logic** for `429 RESOURCE_EXHAUSTED` (Rate Limit) errors. It will retry up to 5 times with increasing delays.

---

## General Score Formula Rationale

The **General Score** is a weighted composite metric designed to provide a quick, high-level assessment of an evaluation run's success. It ranges from **0 to 100**.

### The Formula
```
General Score = 0.4 * goal_completion + 0.2 * trajectory_matcher + 0.2 * behavioral_metrics + 0.2 * parameter_analysis
```

### Rationale for Weights

1.  **Goal Completion (40%)**: This is the most critical factor. Did the agent achieve what it was asked to do? Because of its primary importance, it receives the highest weight.
2.  **Trajectory Matcher (20%)**: Measures how closely the agent followed the expected path or sequence of actions. This helps assess efficiency and adherence to protocols.
3.  **Behavioral Metrics (20%)**: Evaluates the quality of the agent's behavior (e.g., politeness, responsiveness, safety).
4.  **Parameter Analysis (20%)**: Checks if the agent used the correct parameters and constraints in its execution.

By combining these dimensions, the score balances *what* was achieved (Goal Completion) with *how* it was achieved (Trajectory, Behavior, Parameters).

---

## Example Scores and Analysis

Here are three hypothetical examples demonstrating how different runs might be scored and analyzed.

### Example 1: High Performer
-   **Goal Completion**: 100
-   **Trajectory Matcher**: 90
-   **Behavioral Metrics**: 95
-   **Parameter Analysis**: 100
-   **Calculated General Score**: `(0.4 * 100) + (0.2 * 90) + (0.2 * 95) + (0.2 * 100)` = `40 + 18 + 19 + 20` = **97.0**
-   **Analysis**: This run is near perfect. The agent achieved the goal efficiently, followed the expected path closely, exhibited excellent behavior, and adhered to all parameter constraints.

### Example 2: The "Brute Force" Agent
-   **Goal Completion**: 100
-   **Trajectory Matcher**: 40
-   **Behavioral Metrics**: 70
-   **Parameter Analysis**: 60
-   **Calculated General Score**: `(0.4 * 100) + (0.2 * 40) + (0.2 * 70) + (0.2 * 60)` = `40 + 8 + 14 + 12` = **74.0**
-   **Analysis**: The agent successfully completed the goal (100%), but it was highly inefficient (low trajectory score) and violated some parameter constraints. It got the job done, but not in the desired manner.

### Example 3: Failed but Well-Behaved
-   **Goal Completion**: 0
-   **Trajectory Matcher**: 30
-   **Behavioral Metrics**: 90
-   **Parameter Analysis**: 80
-   **Calculated General Score**: `(0.4 * 0) + (0.2 * 30) + (0.2 * 90) + (0.2 * 80)` = `0 + 6 + 18 + 16` = **40.0**
-   **Analysis**: The agent failed to complete the goal. However, it didn't fail catastrophically in terms of behavior or parameter usage. It likely got stuck or stopped early while maintaining good protocol. The low score correctly reflects the failure to achieve the main objective.
