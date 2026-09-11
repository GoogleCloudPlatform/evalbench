#!/usr/bin/env python3
"""Gates the harness smoke build on two tiers of check.

evalbench.eval() exits 0 whenever a run completes, including when the agent
made no tool calls at all, so the exit code alone cannot gate CI.

Tier 1 (non-zero): telemetry scorers must report more than 0. They swallow
parse failures and return 0.0 with an explanation rather than raising, so a
plain liveness check passes even when a CLI renames a token field -- the exact
drift this build exists to catch. Every scenario makes at least one MCP call,
so 0 tokens or 0 latency can only mean the scorer failed to read the output.

Tier 2 (liveness): every remaining scorer must emit a row per scenario, with no
comparison_error and a numeric score. This covers the LLM judges without ever
gating on their verdict, which would make the build flaky.

trajectory_matcher is deliberately liveness-only, not thresholded: which tools
an agent reaches for varies run to run, and a harness may shell out instead of
calling the MCP tool. tool_call_latency > 0 is what proves tools were used.
"""
import csv
import json
import os
import sys

import yaml

HARNESSES = ["agy_cli", "claude_code", "codex_cli", "gemini_cli"]
RUN_CONFIG_DIR = ".ci/run_configs"
EVALSET = ".ci/harness_smoke.evalset.json"
POSITIVE = {
    "turn_count",
    "agent_steps",
    "end_to_end_latency",
    "tool_call_latency",
    "token_consumption",
    "tokens_processed",
    "effective_billed_tokens",
}


def expected_scenario_ids():
    with open(EVALSET) as f:
        return sorted(s["id"] for s in json.load(f)["scenarios"])


def run_config(harness):
    with open(os.path.join(RUN_CONFIG_DIR, f"{harness}.yaml")) as f:
        return yaml.safe_load(f)


def latest_job_dir(output_dir):
    if not os.path.isdir(output_dir):
        return None
    jobs = [os.path.join(output_dir, d) for d in os.listdir(output_dir)
            if os.path.isdir(os.path.join(output_dir, d))]
    return max(jobs, key=os.path.getmtime) if jobs else None


def load_rows(job_dir):
    """Returns {(comparator, scenario_id): (score_or_None, error)}."""
    path = os.path.join(job_dir, "scores.csv")
    if not os.path.exists(path):
        return None
    rows = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                score = float(row["score"])
            except (KeyError, TypeError, ValueError):
                score = None
            rows[(row.get("comparator"), row.get("id"))] = (
                score, (row.get("comparison_error") or "").strip()
            )
    return rows


def check(harness, scenario_ids):
    config = run_config(harness)
    scorers = sorted(config.get("scorers") or {})
    output_dir = config["reporting"]["csv"]["output_directory"]

    job_dir = latest_job_dir(output_dir)
    if job_dir is None:
        return [f"no run output under {output_dir}"], 0, scorers

    rows = load_rows(job_dir)
    if rows is None:
        return [f"no scores.csv in {job_dir}"], 0, scorers

    problems = []
    checked = 0
    for scorer in scorers:
        for sid in scenario_ids:
            entry = rows.get((scorer, sid))
            if entry is None:
                problems.append(f"{scorer}: no row for {sid}")
                continue
            score, error = entry
            checked += 1
            if error:
                problems.append(f"{scorer}: errored on {sid} -- {error[:120]}")
            elif score is None:
                problems.append(f"{scorer}: non-numeric score for {sid}")
            elif scorer in POSITIVE and score <= 0:
                problems.append(
                    f"{scorer}: {sid} reported 0 -- scorer could not read "
                    f"the harness output"
                )
    return problems, checked, scorers


def main():
    scenario_ids = expected_scenario_ids()
    print(f"Scenarios: {len(scenario_ids)} | Harnesses: {len(HARNESSES)}")
    print(f"Must be > 0: {', '.join(sorted(POSITIVE))}")
    print("All other scorers are liveness-checked (ran, no error, "
          "numeric score)\n")

    failed = []
    for harness in HARNESSES:
        problems, checked, scorers = check(harness, scenario_ids)
        if problems:
            failed.append(harness)
            print(f"  [FAIL] {harness}")
            for p in problems:
                print(f"           {p}")
        else:
            print(f"  [PASS] {harness:<12} "
                  f"{checked} checks across {len(scorers)} scorers")

    if failed:
        print(f"\nFAILED: {', '.join(failed)}")
        return 1
    print("\nAll harnesses passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
