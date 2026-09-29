#!/usr/bin/env python3
"""Grades the NL2SQL smoke eval for the verify-nl2sql check.

evalbench.py exits 0 whenever a run completes, so this script reads the CSV
reports and fails the build unless the run is clean:
  1. configs.csv, evals.csv, scores.csv and summary.csv exist and have rows.
  2. Each dataset prompt has exactly one row in evals.csv.
  3. No prompt generator, SQL generator or golden SQL error on any row.
  4. Every row has generated SQL, and returned_sql scores above 0.
  5. Each configured scorer has a numeric score on every row, with no
     comparison_error.
  6. At least one generated query executed (executable_sql above 0).
  7. The report contains pipeline_debug_info from QueryData.

Not gated: generated_error, which executable_sql already scores, and
accuracy, which is printed only. Model variance would make the build flaky.
"""
import csv
import json
import math
import os
import sys

from pyaml_env import parse_config

RUN_CONFIG = os.environ.get("NL2SQL_RUN_CONFIG", ".ci/nl2sql_run_config.yaml")
REPORT_FILES = ("configs.csv", "evals.csv", "scores.csv", "summary.csv")
ERROR_COLUMNS = (
    "prompt_generator_error",
    "sql_generator_error",
    "golden_error",
)
EMPTY = {"", "nan", "none", "null"}


def is_empty(raw):
    return (raw or "").strip().lower() in EMPTY


def as_score(raw):
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    # nan is not a valid score.
    return score if math.isfinite(score) else None


def as_prompt_id(raw):
    # pandas may write id 9 as "9.0".
    text = (raw or "").strip()
    try:
        return str(int(float(text)))
    except ValueError:
        return text


def latest_job_dir(output_dir):
    if not os.path.isdir(output_dir):
        return None
    jobs = [os.path.join(output_dir, d) for d in os.listdir(output_dir)
            if os.path.isdir(os.path.join(output_dir, d))]
    return max(jobs, key=os.path.getmtime) if jobs else None


def read_rows(job_dir, name):
    with open(os.path.join(job_dir, name), newline="") as f:
        return list(csv.DictReader(f))


def check_reports(job_dir):
    problems = []
    for name in REPORT_FILES:
        path = os.path.join(job_dir, name)
        if not os.path.exists(path):
            problems.append(f"{name} is missing")
        elif not read_rows(job_dir, name):
            problems.append(f"{name} has no rows")
    return problems


def check_coverage(evalset, evals):
    with open(evalset) as f:
        expected = {str(item["id"]) for item in json.load(f)}
    counts = {}
    for row in evals:
        prompt_id = as_prompt_id(row.get("prompt_id") or row.get("id"))
        counts[prompt_id] = counts.get(prompt_id, 0) + 1

    problems = []
    missing = sorted(expected - counts.keys())
    if missing:
        problems.append(f"no eval row for prompt(s) {', '.join(missing)}")
    extra = sorted(counts.keys() - expected)
    if extra:
        problems.append(f"eval rows for unknown prompt(s) {', '.join(extra)}")
    duplicated = sorted(p for p, n in counts.items() if n > 1)
    if duplicated:
        problems.append(
            f"more than one eval row for prompt(s) {', '.join(duplicated)}")
    return problems


def check_errors(evals):
    problems = []
    for row in evals:
        eval_id = row.get("id")
        for column in ERROR_COLUMNS:
            value = row.get(column)
            if not is_empty(value):
                problems.append(f"{eval_id}: {column}: {value.strip()[:200]}")
        if is_empty(row.get("generated_sql")):
            problems.append(f"{eval_id}: QueryData returned no SQL")
    return problems


def check_querydata_output(evals):
    problems = []
    for row in evals:
        if "pipeline_debug_info" not in (row.get("other") or ""):
            problems.append(
                f"{row.get('id')}: no pipeline_debug_info in the 'other' "
                "column, so the QueryData output was not reported"
            )
    return problems


def check_scores(scorers, evals, scores):
    by_key = {}
    for row in scores:
        by_key[(row.get("comparator"), row.get("id"))] = row

    problems = []
    for scorer in scorers:
        for eval_row in evals:
            eval_id = eval_row.get("id")
            row = by_key.get((scorer, eval_id))
            if row is None:
                problems.append(f"{scorer}: no score row for {eval_id}")
                continue
            error = (row.get("comparison_error") or "").strip()
            score = as_score(row.get("score"))
            if not is_empty(error):
                problems.append(
                    f"{scorer}: errored on {eval_id}: {error[:200]}")
            elif score is None:
                problems.append(f"{scorer}: non-numeric score for {eval_id}")
            elif scorer == "returned_sql" and score <= 0:
                problems.append(f"returned_sql: scored 0 for {eval_id}")

    if "executable_sql" in scorers:
        executed = [as_score(r.get("score")) for r in scores
                    if r.get("comparator") == "executable_sql"]
        if not any(s is not None and s > 0 for s in executed):
            problems.append(
                "executable_sql: no generated query executed on the CI "
                "database, so the run is broken, not just inaccurate"
            )
    return problems


def print_accuracy(scorers, scores):
    print("Accuracy (reported, not gated):")
    for scorer in scorers:
        values = [as_score(r.get("score")) for r in scores
                  if r.get("comparator") == scorer]
        values = [v for v in values if v is not None]
        if values:
            mean = sum(values) / len(values)
            print(f"  {scorer:<16} mean {mean:6.2f} over {len(values)} rows")


def main():
    config = parse_config(RUN_CONFIG)
    scorers = sorted(config.get("scorers") or {})
    output_dir = config["reporting"]["csv"]["output_directory"]

    job_dir = latest_job_dir(output_dir)
    if job_dir is None:
        print(f"[FAIL] no run output under {output_dir}")
        return 1
    print(f"Job: {job_dir}")

    problems = check_reports(job_dir)
    if problems:
        print("[FAIL]")
        for problem in problems:
            print(f"  {problem}")
        return 1

    evals = read_rows(job_dir, "evals.csv")
    scores = read_rows(job_dir, "scores.csv")
    print(f"Eval rows: {len(evals)}, scorers: {', '.join(scorers)}\n")

    problems = check_coverage(config["dataset_config"], evals)
    problems += check_errors(evals)
    problems += check_querydata_output(evals)
    problems += check_scores(scorers, evals, scores)

    print_accuracy(scorers, scores)
    print()
    if problems:
        print(f"[FAIL] {len(problems)} problem(s)")
        for problem in problems:
            print(f"  {problem}")
        return 1
    print(f"[PASS] clean run: {len(evals)} prompts, {len(scorers)} scorers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
