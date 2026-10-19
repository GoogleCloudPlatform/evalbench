#!/usr/bin/env python3
"""Gates a harness smoke build.

evalbench.eval() exits 0 whenever a run completes, so the exit code alone
cannot gate CI. A channel is a run config and its evalset. By default, the
script checks the MCP-tools and skills channels. It prints one section for
each channel, then one summary table.

Scorers in POSITIVE must report greater than zero. They swallow parse
failures and return 0.0 with an explanation rather than raising, so a
liveness check alone stays green when a CLI renames a token field. A
zero there means the harness shelled out instead of reaching for the
tools or skills it was given (see ZERO_REASONS).

This assumes every scenario requires at least one tool call; a purely
conversational scenario would fail here spuriously.

Every other scorer is liveness-checked only -- it ran, did not error, and
returned a number. Gating on a judge's verdict would make the build flaky.

If the run config has a `reporting.bigquery` block, the check also compares
the run's BigQuery rows with its CSV output. A load job can succeed and still
change values, for example with a type cast.
"""
import argparse
import csv
import json
import math
import os
import sys
import textwrap
import traceback

from pyaml_env import parse_config

DEFAULT_HARNESSES = ["agy_cli", "claude_code", "codex_cli"]
# (name, run config, evalset) for each channel.
DEFAULT_CHANNELS = [
    ("mcp", ".ci/run_config.yaml", ".ci/harness_smoke.evalset.json"),
    ("skills", ".ci/skills_run_config.yaml",
     ".ci/harness_skills.evalset.json"),
]
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
# The Cloud Build log viewer hides blank lines, so separator lines mark the
# sections. RULE starts a section. SUBRULE separates a channel's settings
# from its results.
RULE = "=" * 72
SUBRULE = "  " + "-" * 70


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
    """Returns {(comparator, scenario_id): (score_or_None, error, logs)}."""
    path = os.path.join(job_dir, "scores.csv")
    if not os.path.exists(path):
        return None
    rows = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                score = float(row["score"])
                # nan <= 0 is False, so an unguarded nan would clear the
                # POSITIVE gate.
                if not math.isfinite(score):
                    score = None
            except (KeyError, TypeError, ValueError):
                score = None
            rows[(row.get("comparator"), row.get("id"))] = (
                score,
                (row.get("comparison_error") or "").strip(),
                (row.get("comparison_logs") or "").strip(),
            )
    return rows


def load_summary(job_dir):
    """Returns {metric_name: (correct_results_count, total_results_count)}."""
    path = os.path.join(job_dir, "summary.csv")
    if not os.path.exists(path):
        return {}
    with open(path, newline="") as f:
        return {
            row["metric_name"]: (float(row["correct_results_count"]),
                                 float(row["total_results_count"]))
            for row in csv.DictReader(f)
        }


def _same_score(csv_score, bq_score):
    if csv_score is None or bq_score is None:
        return csv_score is None and bq_score is None
    return math.isclose(csv_score, bq_score, rel_tol=1e-9, abs_tol=1e-9)


def bigquery_dataset(bq_config):
    """Returns the `project.dataset` that the run config uploads to."""
    return (f"{bq_config['gcp_project_id']}."
            f"{bq_config.get('dataset_id') or 'evalbench'}")


def check_bigquery(job_dir, csv_rows, bq_config):
    """Compares the run's BigQuery rows with its CSV output.

    Returns:
      The problems and the number of values compared.
    """
    from google.cloud import bigquery

    job_id = os.path.basename(job_dir)
    project = bq_config["gcp_project_id"]
    dataset = bigquery_dataset(bq_config)
    client = bigquery.Client(project=project)
    job_config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("job_id", "STRING", job_id)])

    # SAFE_CAST works with any column type that autodetect chose, for example
    # STRING for `score`.
    scores_sql = (
        "SELECT comparator, CAST(id AS STRING) AS id, "
        "SAFE_CAST(score AS FLOAT64) AS score "
        f"FROM `{dataset}.scores` WHERE job_id = @job_id")
    summary_sql = (
        "SELECT metric_name, "
        "SAFE_CAST(correct_results_count AS FLOAT64) AS correct, "
        "SAFE_CAST(total_results_count AS FLOAT64) AS total "
        f"FROM `{dataset}.summary` WHERE job_id = @job_id")
    try:
        bq_scores = {}
        for r in client.query(scores_sql, job_config=job_config).result():
            bq_scores.setdefault((r.comparator, r.id), []).append(r.score)
        bq_summary = {
            r.metric_name: (r.correct, r.total)
            for r in client.query(summary_sql, job_config=job_config).result()
        }
    except Exception as e:
        return [f"BigQuery read-back from {dataset} failed: {e}"[:300]], 0

    problems = []
    for (scorer, sid), (score, _, _) in sorted(csv_rows.items()):
        uploaded = bq_scores.pop((scorer, sid), [])
        if not uploaded:
            problems.append(f"BigQuery: no {scorer} row for {sid}")
        elif len(uploaded) > 1:
            problems.append(
                f"BigQuery: {len(uploaded)} {scorer} rows for {sid}")
        elif not _same_score(score, uploaded[0]):
            problems.append(f"BigQuery: {scorer} on {sid} is {uploaded[0]}, "
                            f"but the CSV has {score}")
    for scorer, sid in sorted(bq_scores):
        problems.append(f"BigQuery: {scorer} row for {sid} is not in the CSV")

    csv_summary = load_summary(job_dir)
    for metric, (correct, total) in sorted(csv_summary.items()):
        if metric not in bq_summary:
            problems.append(f"BigQuery: no summary row for {metric}")
            continue
        bq_correct, bq_total = bq_summary[metric]
        # bqstore rounds correct_results_count to INT64 before upload (1.67
        # arrives as 2), so allow a difference of up to 0.5.
        if (bq_total != total or bq_correct is None
                or abs(bq_correct - correct) > 0.5):
            problems.append(
                f"BigQuery: summary for {metric} is {bq_correct}/{bq_total}, "
                f"but the CSV has {correct}/{total}")
    return problems, len(csv_rows) + len(csv_summary)


def check(harness, scenario_ids, config_path):
    """Checks the latest run of one harness in one channel.

    Returns:
      The problems, the number of scorer checks, the number of BigQuery
      comparisons, and the job ID (None if the run wrote no output).
    """
    config = run_config(harness, config_path)
    scorers = sorted(config.get("scorers") or {})
    output_dir = config["reporting"]["csv"]["output_directory"]

    job_dir = latest_job_dir(output_dir)
    if job_dir is None:
        return [f"no run output under {output_dir}"], 0, 0, None
    job_id = os.path.basename(job_dir)

    rows = load_rows(job_dir)
    if rows is None:
        return [f"no scores.csv in {job_dir}"], 0, 0, job_id

    problems = []
    checked = 0
    for scorer in scorers:
        for sid in scenario_ids:
            entry = rows.get((scorer, sid))
            if entry is None:
                problems.append(f"{scorer}: no row for {sid}")
                continue
            score, error, logs = entry
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
                problem = f"{scorer}: {sid} reported 0 -- {reason}"
                # comparison_logs gives the scorer's reason, for example the
                # expected and actual MCP tools for trajectory_matcher.
                if logs:
                    problem += ("\n  comparison_logs: "
                                f"{' '.join(logs.split())[:300]}")
                problems.append(problem)

    bq_checked = 0
    bq_config = config["reporting"].get("bigquery")
    if bq_config:
        bq_problems, bq_checked = check_bigquery(job_dir, rows, bq_config)
        problems.extend(bq_problems)
    return problems, checked, bq_checked, job_id


def check_channel(name, config_path, evalset, harnesses):
    """Prints the section for one channel.

    An error becomes a FAIL line, so the script still checks the other
    harnesses and channels.

    Returns:
      The harnesses that failed in the channel.
    """
    print(RULE)
    print(f"Channel {name}: {config_path} + {evalset}")
    try:
        scenario_ids = expected_scenario_ids(evalset)
        config = run_config(harnesses[0], config_path)
    except Exception:
        print("  [FAIL] cannot read the channel")
        print(textwrap.indent(traceback.format_exc().rstrip(), " " * 11))
        return list(harnesses)

    scorers = sorted(config.get("scorers") or {})
    positive = [s for s in scorers if s in POSITIVE]
    liveness = [s for s in scorers if s not in POSITIVE]
    bq_config = (config.get("reporting") or {}).get("bigquery")
    print(f"  {len(scenario_ids)} scenarios: {', '.join(scenario_ids)}")
    print(f"  Must be > 0: {', '.join(positive) or 'none'}")
    print("  Liveness only (ran, no error, numeric score): "
          f"{', '.join(liveness) or 'none'}")
    if bq_config:
        print(f"  BigQuery: the rows in {bigquery_dataset(bq_config)} must "
              "match the CSV output")
    print(SUBRULE)

    failed = []
    for harness in harnesses:
        try:
            problems, checked, bq_checked, job_id = check(
                harness, scenario_ids, config_path)
        except Exception:
            problems, checked, bq_checked, job_id = (
                ["the check crashed\n" + traceback.format_exc().rstrip()],
                0, 0, None)
        job = f" (job {job_id})" if job_id else ""
        if problems:
            failed.append(harness)
            count = f"{len(problems)} problem{'s' if len(problems) > 1 else ''}"
            print(f"  [FAIL] {harness:<12} {count}{job}")
            for p in problems:
                print(textwrap.indent(p, " " * 11))
        else:
            counts = f"{checked} scorer checks"
            if bq_config:
                counts += f", {bq_checked} BigQuery comparisons"
            print(f"  [PASS] {harness:<12} {counts}{job}")
    return failed


def print_summary(channels, harnesses, failed):
    """Prints a PASS or FAIL cell for each harness in each channel."""
    first = max(len(h) for h in [*harnesses, "harness"]) + 2
    widths = [max(len(c), 4) + 2 for c in channels]
    print(RULE)
    print("Summary")
    header = "  harness".ljust(first + 2) + "".join(
        c.ljust(w) for c, w in zip(channels, widths))
    print(header.rstrip())
    for harness in harnesses:
        cells = ["FAIL" if c in failed.get(harness, []) else "PASS"
                 for c in channels]
        line = f"  {harness}".ljust(first + 2) + "".join(
            s.ljust(w) for s, w in zip(cells, widths))
        print(line.rstrip())


def main():
    parser = argparse.ArgumentParser(description="Gates a harness build.")
    parser.add_argument(
        "--channel", nargs=3, action="append", dest="channels",
        metavar=("NAME", "RUN_CONFIG", "EVALSET"),
        help="a run config and its evalset to check. Repeat for each "
             "channel. Default: the MCP-tools and skills channels.")
    parser.add_argument("--harnesses", nargs="+", default=DEFAULT_HARNESSES)
    args = parser.parse_args()
    # argparse appends to a list default instead of replacing it, so apply
    # the default here.
    channels = args.channels or DEFAULT_CHANNELS
    names = [name for name, _, _ in channels]

    print(f"Harnesses: {', '.join(args.harnesses)}")
    print(f"Channels: {', '.join(names)}")

    failed = {}  # {harness: [channel, ...]}
    for name, config_path, evalset in channels:
        for harness in check_channel(
                name, config_path, evalset, args.harnesses):
            failed.setdefault(harness, []).append(name)

    print_summary(names, args.harnesses, failed)
    if failed:
        print("FAILED: " + ", ".join(
            f"{h} ({', '.join(failed[h])})"
            for h in args.harnesses if h in failed))
        return 1
    print("All harnesses passed in every channel.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
