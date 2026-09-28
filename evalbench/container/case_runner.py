"""In-container entrypoint: run exactly one eval case and report its result.

Invoked by the container backend as::

    python /evalbench/evalbench/container/case_runner.py --case_dir=/etc/evalbench-case

Reads the `EvalCaseSpec` mounted at `--case_dir`, runs the scenario through the
normal `AgentEvaluator` (so generation, the simulated user, and scoring behave
exactly as they do in-process), and writes the result to stdout wrapped in
sentinels the orchestrator greps for. Everything else -- harness logs, agent
chatter -- goes to stderr so the payload stays clean.
"""

import argparse
import datetime
import json
import logging
import os
import sys
import traceback

# Match how supervisord launches eval_server.py: run by path so the package
# root is on sys.path and the flat intra-package imports resolve.
_PACKAGE_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if _PACKAGE_ROOT not in sys.path:
    sys.path.insert(0, _PACKAGE_ROOT)
_PROTO_DIR = os.path.join(_PACKAGE_ROOT, "evalproto")
if os.path.isdir(_PROTO_DIR) and _PROTO_DIR not in sys.path:
    sys.path.insert(0, _PROTO_DIR)

from container.backend import RESULT_BEGIN, RESULT_END  # noqa: E402
from container.spec import EvalCaseSpec  # noqa: E402


def _configure_logging() -> None:
    """Sends every log line to stderr so stdout carries only the payload."""
    logging.basicConfig(
        level=logging.INFO,
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(message)s",
        force=True,
    )
    logging.getLogger("google_genai.models").setLevel(logging.WARNING)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    os.environ.setdefault("GRPC_VERBOSITY", "NONE")

    # Scorers and generators reach for absl logging, which warns (and buffers)
    # until flags are parsed. This process is not an absl app, so mark them
    # parsed with just the program name.
    try:
        from absl import flags

        if not flags.FLAGS.is_parsed():
            flags.FLAGS(sys.argv[:1], known_only=True)
    except Exception:  # absl absent or already configured; not worth failing on
        logging.debug("absl flags not initialized", exc_info=True)


def _emit(payload: dict) -> None:
    sys.stdout.write(f"\n{RESULT_BEGIN}\n")
    json.dump(payload, sys.stdout, default=str)
    sys.stdout.write(f"\n{RESULT_END}\n")
    sys.stdout.flush()


def _materialize_env_files(spec: EvalCaseSpec, session_dir: str) -> None:
    """Recreates the scenario's declared env files where the evaluator looks.

    `AgentEvaluator.process_scenario` copies them out of
    `<parent-of-fake-home>/env_files/`, which in a fresh container is empty
    until we write them back.
    """
    if not spec.env_files:
        return
    env_dir = os.path.join(session_dir, "env_files")
    os.makedirs(env_dir, exist_ok=True)
    for name, content in spec.env_files.items():
        path = os.path.join(env_dir, name)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content)
        logging.info("case: materialized env file %s", path)


def run_case(case_dir: str) -> dict:
    """Runs the case described by `case_dir` and returns its result payload."""
    from dataset.evalgeminicliinput import EvalGeminiCliRequest
    from evaluator.agentevaluator import AgentEvaluator

    spec = EvalCaseSpec.load_from_dir(case_dir)
    config = spec.resolve_model_paths(case_dir)
    logging.info(
        "case: running %s (job %s) on worker pool %s",
        spec.case_id, spec.job_id, os.environ.get("EVALBENCH_WORKER_POOL", "?"))

    evaluator = AgentEvaluator(config)

    # The generator owns the sandbox home; env files live beside it.
    fake_home = getattr(evaluator.generator, "fake_home", None)
    if fake_home:
        _materialize_env_files(spec, os.path.dirname(fake_home))

    work_dir = spec.scenario.get("resolved_work_dir")
    if work_dir:
        os.makedirs(work_dir, exist_ok=True)

    item = EvalGeminiCliRequest(
        id=str(spec.case_id), payload=json.dumps(spec.scenario))

    run_time = _parse_run_time(spec.run_time_iso)
    eval_outputs, scoring_results = evaluator.evaluate(
        [item], spec.job_id, run_time)

    return {
        "case_id": spec.case_id,
        "agent_results": eval_outputs,
        "scoring_results": scoring_results,
        "error": None,
    }


def _parse_run_time(run_time_iso: str) -> datetime.datetime:
    if run_time_iso:
        try:
            return datetime.datetime.fromisoformat(run_time_iso)
        except ValueError:
            logging.warning(
                "case: unparseable run_time %r; using now()", run_time_iso)
    return datetime.datetime.now()


def main(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--case_dir",
        default=os.environ.get("EVALBENCH_CASE_DIR", "/etc/evalbench-case"),
        help="Directory holding the mounted EvalCaseSpec files.",
    )
    args = parser.parse_args(argv)

    _configure_logging()

    case_id = os.environ.get("EVALBENCH_CASE_ID", "unknown")
    try:
        payload = run_case(args.case_dir)
    except Exception as e:
        logging.exception("case: failed to run eval case")
        _emit(
            {
                "case_id": case_id,
                "agent_results": [],
                "scoring_results": [],
                "error": f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
            }
        )
        return 1

    _emit(payload)
    # Surface the failure in the Job status too, while still having emitted the
    # payload so the orchestrator can report *why* it failed.
    return 1 if payload.get("error") else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
