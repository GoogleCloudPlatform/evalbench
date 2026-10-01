#!/usr/bin/env python3
"""Grades the release smoke eval from its CSV reports.

A clean run has:
  1. All four report files, each with rows.
  2. Exactly one eval row for each (dialect, prompt).
  3. No prompt generator error and no golden query error.
  4. Generated SQL and no SQL generator error on every row.
  5. A numeric score with no comparison_error from every scorer on every row.
  6. At least one executed query for each dialect and query type.
Accuracy is printed but not gated, because model variance makes it flaky.

Exit codes: 0 clean, 1 hard failure, 2 only model-dependent checks failed
(4, 6, or LLM judge errors), so a retry can help.

Local test (the CLI runner writes to results/release):
  START=$(date +%s)
  EVAL_CONFIG=.ci/release/release_smoke_config.yaml ./evalbench/run.sh
  uv run python .ci/release/verify_release_smoke.py --since "$START"
"""
import argparse
import ast
import csv
import json
import math
import os
import sys

from pyaml_env import parse_config

DEFAULT_CONFIG = ".ci/release/release_smoke_config.yaml"
REPORT_FILES = ("configs.csv", "evals.csv", "scores.csv", "summary.csv")
HARD_ERROR_COLUMNS = ("prompt_generator_error", "golden_error")
# Scorers that call a model. Their errors are model-dependent.
LLM_SCORERS = {"llmrater"}
EMPTY = {"", "nan", "none", "null"}

HARD = 1
RETRYABLE = 2


def is_empty(raw):
    return (raw or "").strip().lower() in EMPTY


def as_score(raw):
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    return score if math.isfinite(score) else None


def as_prompt_id(raw):
    """pandas can write id 9 as "9.0"."""
    text = (raw or "").strip()
    try:
        return str(int(float(text)))
    except ValueError:
        return text


def as_dialect(raw):
    """The CSV stores the dialects list as its repr, e.g. "['sqlite']"."""
    try:
        parsed = ast.literal_eval(raw or "")
    except (ValueError, SyntaxError):
        parsed = raw
    if isinstance(parsed, (list, tuple)):
        return ",".join(str(d) for d in parsed)
    return str(parsed or "")


def expected_prompts(config):
    """Returns {(dialect, prompt id): query type} that the run must cover.

    Filters like dataset.py: by the run config dialects and query types, if
    the lists are not empty.
    """
    with open(config["dataset_config"]) as f:
        data = json.load(f)
    items = data["scenarios"] if isinstance(data, dict) else data
    dialects = config.get("dialects") or []
    query_types = [q.lower() for q in config.get("query_types") or []]
    expected = {}
    for item in items:
        query_type = item["query_type"].lower()
        if query_types and query_type not in query_types:
            continue
        for dialect in item.get("dialects") or []:
            if not dialects or dialect in dialects:
                expected[(dialect, str(item["id"]))] = query_type
    return expected


def job_dirs(results_dir, since):
    """Returns job directories changed after `since`, newest first."""
    if not os.path.isdir(results_dir):
        return []
    dirs = [os.path.join(results_dir, d) for d in os.listdir(results_dir)]
    dirs = [d for d in dirs
            if os.path.isdir(d) and os.path.getmtime(d) >= since]
    return sorted(dirs, key=os.path.getmtime, reverse=True)


def read_rows(job_dir, name):
    with open(os.path.join(job_dir, name), newline="") as f:
        return list(csv.DictReader(f))


def eval_key(row):
    return (as_dialect(row.get("dialects")),
            as_prompt_id(row.get("prompt_id") or row.get("id")))


def find_job(results_dir, since, expected):
    """Returns the newest job whose evals.csv covers an expected prompt."""
    for job_dir in job_dirs(results_dir, since):
        path = os.path.join(job_dir, "evals.csv")
        if not os.path.exists(path):
            continue
        keys = {eval_key(row) for row in read_rows(job_dir, "evals.csv")}
        if keys & expected.keys():
            return job_dir
    return None


class Problems:
    """Collects failures and remembers whether any of them is hard."""

    def __init__(self):
        self.items = []
        self.hard = False

    def add(self, message, retryable=False):
        self.items.append(message + ("  [model]" if retryable else ""))
        self.hard = self.hard or not retryable


def check_reports(job_dir, problems):
    for name in REPORT_FILES:
        path = os.path.join(job_dir, name)
        if not os.path.exists(path):
            problems.add(f"{name} is missing")
        elif not read_rows(job_dir, name):
            problems.add(f"{name} has no rows")


def check_coverage(expected, evals, problems):
    counts = {}
    for row in evals:
        counts[eval_key(row)] = counts.get(eval_key(row), 0) + 1
    for key in sorted(expected.keys() - counts.keys()):
        problems.add(f"no eval row for prompt {key[1]} [{key[0]}]")
    for key in sorted(counts.keys() - expected.keys()):
        problems.add(f"eval row for unexpected prompt {key[1]} [{key[0]}]")
    for key, count in sorted(counts.items()):
        if count > 1:
            problems.add(f"{count} eval rows for prompt {key[1]} [{key[0]}]")


def check_errors(evals, problems):
    for row in evals:
        target = "{1} [{0}]".format(*eval_key(row))
        for column in HARD_ERROR_COLUMNS:
            if not is_empty(row.get(column)):
                problems.add(
                    f"{target}: {column}: {row[column].strip()[:200]}")
        if not is_empty(row.get("sql_generator_error")):
            problems.add(f"{target}: sql_generator_error: "
                         f"{row['sql_generator_error'].strip()[:200]}",
                         retryable=True)
        elif is_empty(row.get("generated_sql")):
            problems.add(f"{target}: the model returned no SQL",
                         retryable=True)


def check_scores(scorers, evals, scores, problems):
    by_key = {}
    for row in scores:
        by_key[(row.get("comparator"), row.get("id"),
                as_dialect(row.get("dialects")))] = row
    for scorer in scorers:
        retryable = scorer in LLM_SCORERS
        for eval_row in evals:
            dialect, prompt_id = eval_key(eval_row)
            target = f"{prompt_id} [{dialect}]"
            row = by_key.get((scorer, eval_row.get("id"), dialect))
            if row is None:
                problems.add(f"{scorer}: no score row for {target}")
                continue
            error = row.get("comparison_error")
            if not is_empty(error):
                problems.add(f"{scorer}: errored on {target}: "
                             f"{error.strip()[:200]}", retryable=retryable)
            elif as_score(row.get("score")) is None:
                problems.add(f"{scorer}: non-numeric score for {target}",
                             retryable=retryable)


def check_execution(expected, evals, scores, problems):
    """Requires one executed generated query per dialect and query type."""
    executed = {(r.get("id"), as_dialect(r.get("dialects")))
                for r in scores
                if r.get("comparator") == "executable_sql"
                and (as_score(r.get("score")) or 0) > 0}
    groups = {}
    for row in evals:
        key = eval_key(row)
        query_type = expected.get(key)
        if query_type is None:
            continue
        group = groups.setdefault((key[0], query_type), [])
        group.append((row.get("id"), key[0]) in executed)
    for (dialect, query_type), results in sorted(groups.items()):
        if not any(results):
            problems.add(f"executable_sql: no generated {query_type} query "
                         f"executed on {dialect}", retryable=True)


def print_accuracy(scorers, scores):
    print("Accuracy (reported, not gated):")
    for scorer in scorers:
        values = [as_score(r.get("score")) for r in scores
                  if r.get("comparator") == scorer]
        values = [v for v in values if v is not None]
        if values:
            print(f"  {scorer:<16} mean {sum(values) / len(values):6.2f} "
                  f"over {len(values)} rows")


def verify(config_path, results_dir, since):
    config = parse_config(config_path)
    scorers = sorted(config.get("scorers") or {})
    results_dir = results_dir or config["reporting"]["csv"]["output_directory"]
    expected = expected_prompts(config)
    if not expected:
        print(f"[FAIL] {config['dataset_config']} has no prompts for "
              f"dialects {config.get('dialects')} and query types "
              f"{config.get('query_types')}")
        return HARD

    job_dir = find_job(results_dir, since, expected)
    if job_dir is None:
        print(f"[FAIL] no evals.csv under {results_dir} covers the dataset")
        return HARD
    print(f"Job: {job_dir}")

    problems = Problems()
    check_reports(job_dir, problems)
    if not problems.items:
        evals = read_rows(job_dir, "evals.csv")
        scores = read_rows(job_dir, "scores.csv")
        print(f"Eval rows: {len(evals)}, scorers: {', '.join(scorers)}\n")
        check_coverage(expected, evals, problems)
        check_errors(evals, problems)
        check_scores(scorers, evals, scores, problems)
        check_execution(expected, evals, scores, problems)
        print_accuracy(scorers, scores)
        print()

    if not problems.items:
        print(f"[PASS] clean run: {len(expected)} prompts, "
              f"{len(scorers)} scorers")
        return 0
    kind = "hard" if problems.hard else "model-dependent"
    print(f"[FAIL] {len(problems.items)} problem(s), {kind}")
    for problem in problems.items:
        print(f"  {problem}")
    return HARD if problems.hard else RETRYABLE


def main():
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--config", default=DEFAULT_CONFIG,
                        help="Run config of the smoke eval.")
    parser.add_argument("--results-dir",
                        help="Overrides reporting.csv.output_directory. "
                             "The gRPC server writes to "
                             "/tmp_session_files/results.")
    parser.add_argument("--since", type=float, default=0.0,
                        help="Ignore job directories older than this Unix "
                             "time, so a retry does not grade an old job.")
    args = parser.parse_args()
    return verify(args.config, args.results_dir, args.since)


if __name__ == "__main__":
    sys.exit(main())
