#!/usr/bin/env python3
"""Gates the weekly release build on the smoke run's scores.csv.

evalbench exits 0 when a run completes, so exit code alone cannot gate a
release. This script audits scores.csv in two tiers:
  - Tier 1 (score > 0): structural scorers (returned_sql, executable_sql).
  - Tier 2 (liveness): scorer produced a finite number with no comparison_error.

When eval runs via eval_server.py, CsvReporter writes to
/tmp_session_files/results. Pass --results-dir to override the config path.
"""
import argparse
import ast
import csv
import json
import math
import os
import re
import sys

from pyaml_env import parse_config

# CsvReporter names each trial row "<dataset id>_trial_<n>".
_TRIAL_SUFFIX = re.compile(r"^(?P<base>.+)_trial_\d+$")

LEGS = {
    "oneshot": {
        "config": ".ci/release/release_smoke_config.yaml",
        "positive": {"returned_sql", "executable_sql"},
    },
}


def base_id(row_id):
    """Strips the _trial_N suffix from a row id."""
    match = _TRIAL_SUFFIX.match(row_id or "")
    return match.group("base") if match else (row_id or "")


def row_dialect(raw):
    """scores.csv stores the dialects list as its repr, e.g. "['sqlite']"."""
    try:
        parsed = ast.literal_eval(raw or "")
    except (ValueError, SyntaxError):
        parsed = raw
    if isinstance(parsed, (list, tuple)):
        return ",".join(str(dialect) for dialect in parsed)
    return str(parsed or "")


def expected_keys(dataset_config, config_dialects):
    """Returns the {(dialect, scenario id)} pairs the run should score.

    A scenario runs once per dialect, and dataset.py intersects the scenario's
    own dialects with config['dialects'] when that list is non-empty.
    """
    with open(dataset_config) as f:
        data = json.load(f)
    items = data["scenarios"] if isinstance(data, dict) else data
    keys = set()
    for item in items:
        dialects = item.get("dialects") or []
        if config_dialects:
            dialects = [d for d in dialects if d in config_dialects]
        keys.update((dialect, str(item["id"])) for dialect in dialects)
    return keys


def job_dirs(results_dir):
    """Returns job directories under results_dir, newest first."""
    if not os.path.isdir(results_dir):
        return []
    dirs = [os.path.join(results_dir, d) for d in os.listdir(results_dir)]
    dirs = [d for d in dirs if os.path.isdir(d)]
    return sorted(dirs, key=os.path.getmtime, reverse=True)


def load_rows(job_dir):
    """Returns {(dialect, comparator, base_id): (score_or_None, error)}, or None."""
    path = os.path.join(job_dir, "scores.csv")
    if not os.path.exists(path):
        return None
    rows = {}
    with open(path, newline="") as f:
        for row in csv.DictReader(f):
            try:
                score = float(row["score"])
                if not math.isfinite(score):
                    score = None
            except (KeyError, TypeError, ValueError):
                score = None
            key = (row_dialect(row.get("dialects")),
                   row.get("comparator"),
                   base_id(row.get("id")))
            rows[key] = (score, (row.get("comparison_error") or "").strip())
    return rows


def find_job(results_dir, keys):
    """Returns (job_dir, rows) for the newest job covering every expected key."""
    for job_dir in job_dirs(results_dir):
        rows = load_rows(job_dir)
        if rows is None:
            continue
        if keys <= {(dialect, row_id) for dialect, _, row_id in rows}:
            return job_dir, rows
    return None, None


def check(leg_name, leg, results_dir_override):
    config = parse_config(leg["config"])
    scorers = sorted(config.get("scorers") or {})
    results_dir = results_dir_override or config["reporting"]["csv"][
        "output_directory"]
    keys = expected_keys(config["dataset_config"], config.get("dialects") or [])
    if not keys:
        return [f"{config['dataset_config']} has no scenarios matching "
                f"dialects {config.get('dialects')}"], 0, scorers, None

    job_dir, rows = find_job(results_dir, keys)
    if job_dir is None:
        return [f"no scores.csv under {results_dir} covering "
                f"{sorted(keys)}"], 0, scorers, None

    problems = []
    checked = 0
    for scorer in scorers:
        for dialect, scenario_id in sorted(keys):
            target = f"{scenario_id} [{dialect}]"
            entry = rows.get((dialect, scorer, scenario_id))
            if entry is None:
                problems.append(f"{scorer}: no row for {target}")
                continue
            score, error = entry
            checked += 1
            if error:
                problems.append(
                    f"{scorer}: errored on {target} -- {error[:120]}")
            elif score is None:
                problems.append(
                    f"{scorer}: non-numeric score for {target}")
            elif scorer in leg["positive"] and score <= 0:
                problems.append(
                    f"{scorer}: {target} reported 0 -- the released "
                    f"image could not complete this step")
    return problems, checked, scorers, job_dir


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--leg", action="append", choices=sorted(LEGS),
                        help="Leg to verify. Repeatable. Defaults to all.")
    parser.add_argument("--results-dir",
                        help="Overrides reporting.csv.output_directory. "
                             "Required when the run went through "
                             "eval_server.py.")
    args = parser.parse_args()

    legs = args.leg or sorted(LEGS)
    failed = []
    for leg_name in legs:
        leg = LEGS[leg_name]
        problems, checked, scorers, job_dir = check(
            leg_name, leg, args.results_dir)
        print(f"{leg_name}  ({leg['config']})")
        print(f"  must be > 0: {', '.join(sorted(leg['positive']))}")
        if job_dir:
            print(f"  job: {job_dir}")
        if problems:
            failed.append(leg_name)
            print("  [FAIL]")
            for problem in problems:
                print(f"    {problem}")
        else:
            print(f"  [PASS] {checked} checks across {len(scorers)} scorers")
        print()

    if failed:
        print(f"FAILED: {', '.join(failed)}")
        return 1
    print("Release smoke gate passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
