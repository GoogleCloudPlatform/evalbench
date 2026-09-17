#!/usr/bin/env python3
"""Gates the NL2SQL smoke build on two tiers of check.

evalbench.eval() exits 0 whenever a run completes, and the orchestrator
turns a whole failed query type into a log line rather than an exception,
so the exit code alone cannot gate CI.

Tier 1 (structural): returned_sql and executable_sql must report more than
0 for dql, dml and ddl separately. A single aggregate would be carried by
the dql rows and stay green while dml and ddl failed outright.

Tier 2 (liveness) covers every other scorer, including the LLM judges,
without gating on their verdict, which would make the build flaky on
ordinary model variance.

generated_error is deliberately not checked. A model writing invalid SQL
is a scored outcome, and executable_sql already maps it to 0.
"""
import csv
import json
import math
import os
import sys

from pyaml_env import parse_config

RUN_CONFIG = ".ci/nl2sql_run_config.yaml"
EVALSET = ".ci/nl2sql_smoke.evalset.json"
QUERY_TYPES = ["dql", "dml", "ddl"]
STRUCTURAL = {"returned_sql", "executable_sql"}
# Scored per prompt across trials, so these rows carry prompt_id instead of
# id and have no comparison_error column.
MULTI_TRIAL = {"exact_match_consistency", "llm_consistency"}


def latest_job_dir(output_dir):
    if not os.path.isdir(output_dir):
        return None
    jobs = [os.path.join(output_dir, d) for d in os.listdir(output_dir)
            if os.path.isdir(os.path.join(output_dir, d))]
    return max(jobs, key=os.path.getmtime) if jobs else None


def as_score(raw):
    try:
        score = float(raw)
    except (TypeError, ValueError):
        return None
    # nan <= 0 is False, so an unguarded nan would clear the Tier 1 gate.
    return score if math.isfinite(score) else None


def as_prompt_id(raw):
    # prompt_id is blank on per-eval rows, so pandas types the whole column
    # as float and writes 9 as "9.0" in scores.csv but "9" in evals.csv.
    text = (raw or "").strip()
    try:
        return str(int(float(text)))
    except ValueError:
        return text


def load_evals(job_dir):
    """Returns {query_type: {eval_id: prompt_id}}."""
    by_type = {qt: {} for qt in QUERY_TYPES}
    with open(os.path.join(job_dir, "evals.csv"), newline="") as f:
        for row in csv.DictReader(f):
            query_type = (row.get("query_type") or "").strip().lower()
            if query_type in by_type:
                by_type[query_type][row.get("id")] = as_prompt_id(
                    row.get("prompt_id"))
    return by_type


def load_scores(job_dir):
    """Returns per-eval {(comparator, id): (score, error)} and multi-trial
    {(comparator, prompt_id): [score, ...]}.
    """
    per_eval = {}
    multi = {}
    with open(os.path.join(job_dir, "scores.csv"), newline="") as f:
        for row in csv.DictReader(f):
            comparator = row.get("comparator")
            score = as_score(row.get("score"))
            if comparator in MULTI_TRIAL:
                key = (comparator, as_prompt_id(row.get("prompt_id")))
                multi.setdefault(key, []).append(score)
            else:
                per_eval[(comparator, row.get("id"))] = (
                    score, (row.get("comparison_error") or "").strip()
                )
    return per_eval, multi


def check_dataset_coverage(evals_by_type):
    with open(EVALSET) as f:
        expected = {str(item["id"]) for item in json.load(f)}
    seen = set()
    for query_type in QUERY_TYPES:
        seen.update(evals_by_type[query_type].values())
    missing = sorted(expected - seen)
    if missing:
        return [f"no eval rows for prompt(s) {', '.join(missing)}"]
    return []


def check_tier1(evals_by_type, per_eval):
    problems = []
    for query_type in QUERY_TYPES:
        eval_ids = evals_by_type[query_type]
        if not eval_ids:
            problems.append(
                f"{query_type}: no eval rows, so the whole {query_type} "
                "sub-dataset failed before scoring"
            )
            continue
        for scorer in sorted(STRUCTURAL):
            scores = [per_eval.get((scorer, eval_id), (None, ""))[0]
                      for eval_id in eval_ids]
            scores = [s for s in scores if s is not None]
            if not scores:
                problems.append(
                    f"{scorer}: no numeric score on any {query_type} row"
                )
            elif max(scores) <= 0:
                reason = (
                    "the model returned no SQL"
                    if scorer == "returned_sql"
                    else "no generated query executed against the database"
                )
                problems.append(
                    f"{scorer}: every {query_type} row scored 0, {reason}"
                )
    return problems


def check_tier2(scorers, evals_by_type, per_eval, multi):
    problems = []
    checked = 0

    eval_ids = {}
    for query_type in QUERY_TYPES:
        eval_ids.update(evals_by_type[query_type])
    # Trials only multiply the dql prompts, so those are the only prompts
    # the consistency scorers can produce a pair for.
    dql_prompt_ids = sorted(set(evals_by_type["dql"].values()))

    for scorer in scorers:
        if scorer in MULTI_TRIAL:
            for prompt_id in dql_prompt_ids:
                scores = multi.get((scorer, prompt_id))
                if not scores:
                    problems.append(
                        f"{scorer}: no row for prompt {prompt_id}")
                    continue
                checked += len(scores)
                if any(score is None for score in scores):
                    problems.append(
                        f"{scorer}: non-numeric score for prompt {prompt_id}"
                    )
            continue
        for eval_id in sorted(eval_ids):
            entry = per_eval.get((scorer, eval_id))
            if entry is None:
                problems.append(f"{scorer}: no row for {eval_id}")
                continue
            score, error = entry
            checked += 1
            if error:
                problems.append(
                    f"{scorer}: errored on {eval_id}, {error[:120]}")
            elif score is None:
                problems.append(f"{scorer}: non-numeric score for {eval_id}")
    return problems, checked


def main():
    config = parse_config(RUN_CONFIG)
    scorers = sorted(config.get("scorers") or {})
    output_dir = config["reporting"]["csv"]["output_directory"]

    job_dir = latest_job_dir(output_dir)
    if job_dir is None:
        print(f"[FAIL] no run output under {output_dir}")
        return 1
    for name in ("evals.csv", "scores.csv"):
        if not os.path.exists(os.path.join(job_dir, name)):
            print(f"[FAIL] no {name} in {job_dir}")
            return 1

    evals_by_type = load_evals(job_dir)
    per_eval, multi = load_scores(job_dir)

    print(f"Job: {job_dir}")
    counts = ", ".join(
        f"{qt} {len(evals_by_type[qt])}" for qt in QUERY_TYPES)
    print(f"Eval rows: {counts}")
    print(f"Must be > 0 per query type: {', '.join(sorted(STRUCTURAL))}")
    print("All other scorers are liveness-checked (ran, no error, numeric "
          "score)\n")

    problems = check_dataset_coverage(evals_by_type)
    problems += check_tier1(evals_by_type, per_eval)
    tier2_problems, checked = check_tier2(
        scorers, evals_by_type, per_eval, multi)
    problems += tier2_problems

    if problems:
        print("  [FAIL]")
        for problem in problems:
            print(f"           {problem}")
        return 1
    print(f"  [PASS] {checked} checks across {len(scorers)} scorers")
    return 0


if __name__ == "__main__":
    sys.exit(main())
