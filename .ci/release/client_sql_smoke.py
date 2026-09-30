#!/usr/bin/env python3
"""Runs the client-generated SQL path on the local eval server.

Most production clients generate SQL themselves and send it back. The server
uses the noop generator, reads its configs from the EvalConfig resources, and
only executes and scores the SQL. This script does the same with fixed SQL,
so every score is known before the run:

  batch pass      golden SQL on every item. Every scorer gives 100.
  streaming pass  wrong SQL on the last item. exact_match and set_match give
                  0 for it, which proves that the scorers can fail.

No model is called, so a failure is never model variance.

Exit codes: 0 every score matches, 1 otherwise.

Local test, with the server from 'python evalbench/eval_server.py --localhost':
  EVALBENCH_INSECURE=true python .ci/release/client_sql_smoke.py
"""
import argparse
import asyncio
import os
import sys

import yaml

_HERE = os.path.dirname(os.path.abspath(__file__))
_REPO = os.path.dirname(os.path.dirname(_HERE))
# Add both module roots so eval_client and generated proto imports resolve.
for _path in (os.path.join(_REPO, "evalbench"),
              os.path.join(_REPO, "evalbench", "evalproto")):
    if _path not in sys.path:
        sys.path.insert(0, _path)

from client.eval_client import EvalbenchClient  # noqa: E402
from evalproto import eval_config_pb2, eval_connect_pb2  # noqa: E402
from evalproto import eval_request_pb2  # noqa: E402
from verify_release_smoke import (  # noqa: E402
    HARD_ERROR_COLUMNS, as_prompt_id, as_score, is_empty, read_rows)

# The server replaces a config value that equals a resource address with the
# path of that resource in its session directory.
ROOT = "release_smoke"
DIALECT = "sqlite"
# Resource address -> repo file. None means the content is in RESOURCES_TEXT.
RESOURCES = {
    f"{ROOT}/release_smoke.evalset.json":
        ".ci/release/release_smoke.evalset.json",
    f"{ROOT}/sqlite.yaml": "datasets/bat/db_configs/sqlite.yaml",
    f"{ROOT}/noop_model.yaml": None,
}
RESOURCES_TEXT = {f"{ROOT}/noop_model.yaml": "generator: noop\n"}
CONFIG = {
    "dataset_config": f"{ROOT}/release_smoke.evalset.json",
    "dataset_format": "evalbench-standard-format",
    "database_configs": [f"{ROOT}/sqlite.yaml"],
    "dialects": [DIALECT],
    "dialect": DIALECT,
    # The orchestrator sends read queries only. DML goldens also write
    # CURRENT_TIMESTAMP, so their scores are not fixed.
    "query_types": ["dql"],
    "setup_directory": "datasets/bat/setup",
    "model_config": f"{ROOT}/noop_model.yaml",
    "prompt_generator": "NOOPGenerator",
    "num_trials": 1,
    "scorers": {"exact_match": None, "set_match": None,
                "executable_sql": None, "returned_sql": None},
    "reporting": {"csv": {"output_directory": "results"}},
}
WRONG_SQL = "SELECT -1 AS wrong_answer;"
# Expected scores for the item with WRONG_SQL. Golden SQL gives 100 on all.
WRONG_SCORES = {"exact_match": 0, "set_match": 0,
                "executable_sql": 100, "returned_sql": 100}


def config_request():
    request = eval_config_pb2.EvalConfigRequest(
        yaml_config=yaml.safe_dump(CONFIG).encode("utf-8"))
    for address, path in RESOURCES.items():
        if path is None:
            content = RESOURCES_TEXT[address].encode("utf-8")
        else:
            with open(os.path.join(_REPO, path), "rb") as f:
                content = f.read()
        request.resources.add(address=address, content=content)
    return request


async def run_pass(streaming, wrong_last):
    """Returns (job_id, {item id: sent SQL}) for one Eval call."""
    client = EvalbenchClient("local")
    metadata = client.metadata
    try:
        await client.stub.Ping(eval_request_pb2.PingRequest(),
                               metadata=metadata)
        await client.stub.Connect(
            eval_connect_pb2.EvalConnectRequest(
                client_id="release-smoke", streaming_eval=streaming),
            metadata=metadata)
        await client.stub.EvalConfig(config_request(), metadata=metadata)
        items = [item async for item in client.stub.ListEvalInputs(
            eval_request_pb2.EvalInputRequest(), metadata=metadata)]
        if not items:
            raise RuntimeError("ListEvalInputs returned no items")
        sent = {}
        for i, item in enumerate(items):
            golden = item.golden_sql[DIALECT].sql_statements[0]
            wrong = wrong_last and i == len(items) - 1
            item.generated_sql = WRONG_SQL if wrong else golden
            sent[str(item.id)] = item.generated_sql
        response = await client.stub.Eval(iter(items), metadata=metadata)
        return response.response, sent
    finally:
        await client.channel.close()


def check(name, results_dir, job_id, sent, wrong_id):
    """Returns the problems in the reports of one pass."""
    job_dir = os.path.join(results_dir, job_id)
    if not job_id or not os.path.isdir(job_dir):
        return [f"no report directory {job_dir}"]
    problems = []
    # prompt_id is the dataset id. id is the eval id that scores.csv uses.
    evals = {as_prompt_id(r.get("prompt_id")): r
             for r in read_rows(job_dir, "evals.csv")}
    eval_ids = {}
    for item_id, sql in sorted(sent.items()):
        row = evals.get(item_id)
        if row is None:
            problems.append(f"{item_id}: no eval row")
            continue
        eval_ids[item_id] = as_prompt_id(row.get("id"))
        for column in HARD_ERROR_COLUMNS + ("sql_generator_error",
                                            "generated_error"):
            if not is_empty(row.get(column)):
                problems.append(f"{item_id}: {column}: "
                                f"{row[column].strip()[:200]}")
        # The noop generator must keep the client SQL.
        if (row.get("generated_sql") or "").strip() != sql.strip():
            problems.append(f"{item_id}: generated_sql is not the SQL that "
                            f"the client sent")

    scores = {(r.get("comparator"), as_prompt_id(r.get("id"))): r
              for r in read_rows(job_dir, "scores.csv")}
    for scorer in sorted(CONFIG["scorers"]):
        for item_id, eval_id in sorted(eval_ids.items()):
            want = WRONG_SCORES[scorer] if item_id == wrong_id else 100
            row = scores.get((scorer, eval_id))
            got = as_score(row.get("score")) if row else None
            if got != want:
                problems.append(f"{scorer}: {item_id} scored {got}, "
                                f"expected {want}")
    status = "[PASS]" if not problems else "[FAIL]"
    print(f"{status} {name}: job {job_id}, {len(sent)} items, "
          f"{len(problems)} problem(s)")
    for problem in problems:
        print(f"  {problem}")
    return problems


async def run(results_dir):
    failed = False
    for name, streaming, wrong_last in (("batch pass", False, False),
                                        ("streaming pass", True, True)):
        try:
            job_id, sent = await run_pass(streaming, wrong_last)
        except Exception as e:
            print(f"[FAIL] {name}: {type(e).__name__}: {e}")
            failed = True
            continue
        wrong_id = list(sent)[-1] if wrong_last else None
        failed |= bool(check(name, results_dir, job_id, sent, wrong_id))
    return 1 if failed else 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    # CsvReporter forces this directory on the gRPC path.
    parser.add_argument("--results-dir", default="/tmp_session_files/results",
                        help="Directory that holds <job_id>/scores.csv.")
    args = parser.parse_args()
    return asyncio.run(run(args.results_dir))


if __name__ == "__main__":
    sys.exit(main())
