#!/usr/bin/env python3
"""Gates a harness smoke build.

evalbench.eval() exits 0 whenever a run completes, so the exit code alone
cannot gate CI. The defaults target the MCP-tools build; --run-config and
--evalset aim the same checks at the skills build.

Scorers in POSITIVE must report greater than zero. They swallow parse
failures and return 0.0 with an explanation rather than raising, so a
liveness check alone stays green when a CLI renames a token field. A
trajectory_matcher of 0 means none of the expected tools were called,
which catches a harness that shells out instead of reaching for MCP;
skills_trajectory of 0 is the same signal for the skills channel.

This assumes every scenario requires at least one tool call; a purely
conversational scenario would fail here spuriously.

Every other scorer is liveness-checked only -- it ran, did not error, and
returned a number. Gating on a judge's verdict would make the build flaky.
"""
import argparse
import csv
import json
import math
import os
import sys

from pyaml_env import parse_config

DEFAULT_HARNESSES = ["agy_cli", "claude_code", "codex_cli", "gemini_cli"]
DEFAULT_RUN_CONFIG = ".ci/run_config.yaml"
DEFAULT_EVALSET = ".ci/harness_smoke.evalset.json"
POSITIVE = {
    "trajectory_matcher",
    "skills_trajectory",
    "turn_count",
    "agent_steps",
    "end_to_end_latency",
    "tool_call_latency",
    "token_consumption",
    "tokens_processed",
    "effective_billed_tokens",
}
ZERO_REASONS = {
    "trajectory_matcher": "none of the expected tools were called",
    "skills_trajectory": "none of the expected skills were activated",
}


def expected_scenario_ids(evalset):
    with open(evalset) as f:
        return sorted(s["id"] for s in json.load(f)["scenarios"])


def run_config(harness, path):
    # The run config resolves ${CI_HARNESS} into the model config and output
    # paths, so it has to be re-parsed per harness.
    os.environ["CI_HARNESS"] = harness
    return parse_config(path)


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
                # nan <= 0 is False, so an unguarded nan would clear the
                # Tier 1 gate.
                if not math.isfinite(score):
                    score = None
            except (KeyError, TypeError, ValueError):
                score = None
            rows[(row.get("comparator"), row.get("id"))] = (
                score, (row.get("comparison_error") or "").strip()
            )
    return rows


def check(harness, scenario_ids, config_path):
    config = run_config(harness, config_path)
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
                reason = ZERO_REASONS.get(
                    scorer,
                    "the agent called no tools, or the scorer could not "
                    "read the harness output",
                )
                problems.append(f"{scorer}: {sid} reported 0 -- {reason}")
    return problems, checked, scorers


def main():
    parser = argparse.ArgumentParser(description="Gates a harness build.")
    parser.add_argument("--run-config", default=DEFAULT_RUN_CONFIG)
    parser.add_argument("--evalset", default=DEFAULT_EVALSET)
    parser.add_argument("--harnesses", nargs="+", default=DEFAULT_HARNESSES)
    args = parser.parse_args()

    scenario_ids = expected_scenario_ids(args.evalset)
    print(f"Scenarios: {len(scenario_ids)} | "
          f"Harnesses: {len(args.harnesses)}")
    print(f"Must be > 0 where configured: {', '.join(sorted(POSITIVE))}")
    print("All other scorers are liveness-checked (ran, no error, "
          "numeric score)\n")

    failed = []
    for harness in args.harnesses:
        problems, checked, scorers = check(
            harness, scenario_ids, args.run_config)
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
