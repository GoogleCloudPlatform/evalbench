"""Runs each agent eval case in its own container on a dedicated worker pool.

Drop-in alternative to `AgentEvaluator` with the same
`evaluate(dataset, job_id, run_time) -> (eval_outputs, scoring_results)`
contract. Instead of running every scenario as a subprocess inside the eval
server pod, it packages each scenario as an `EvalCaseSpec`, dispatches it to a
container on a worker pool (round-robin), and collects the rows the container
produced.

Only Claude Code is supported today: it is the generator whose sandbox
(a shared `fake_home`, `--fork-session` to keep concurrent scenarios from
colliding) benefits most from real per-case isolation.
"""

from typing import Any, Optional
import concurrent.futures
import datetime
import json
import logging
import os
import sys
import threading

from container.backend import (
    CaseResult,
    ContainerBackend,
    acquire_shared_backend,
    release_shared_backend,
)
from container.config import ContainerizationConfig, load_containerization_config
from container.pool_router import WorkerPoolRouter, shared_slots
from container.spec import EvalCaseSpec
from dataset.evalgeminicliinput import EvalGeminiCliRequest
from util.config import get_eval_case_timeout, load_yaml_config
from util.context import rpc_id_var

SUPPORTED_GENERATORS = frozenset({"claude_code"})


class ContainerAgentEvaluator:
    """Fans eval cases out to one container each, across worker pools."""

    def __init__(
        self,
        config: dict,
        containerization: Optional[ContainerizationConfig] = None,
        backend: Optional[ContainerBackend] = None,
    ) -> None:
        self.config = config
        self.eval_case_timeout_seconds = get_eval_case_timeout(config)
        self.containerization = (
            containerization
            if containerization is not None
            else load_containerization_config(config)
        )
        if not self.containerization.enabled:
            raise ValueError(
                "ContainerAgentEvaluator requires `containerization.enabled: true`")

        self.generator_name = self._validate_generator(config)
        # Slots are process-wide: the server runs many eval sessions at once
        # and `max_concurrent_per_pool` has to bound the cluster, not just this
        # one session's view of it.
        self.router = WorkerPoolRouter(
            self.containerization,
            slots=shared_slots(self.containerization),
        )
        self._backend = backend
        self._owns_backend = backend is None
        self._lock = threading.Lock()

    @staticmethod
    def _validate_generator(config: dict) -> str:
        """Checks the model config names a supported generator.

        Reads the YAML rather than instantiating the generator: constructing
        `ClaudeCodeGenerator` sets up a sandbox home, copies credentials and
        starts MCP servers, all of which belong in the case container, not the
        orchestrator.
        """
        model_config_path = config.get("model_config")
        if not isinstance(model_config_path, str):
            raise ValueError(
                "Containerized agent evaluation requires `model_config` to be "
                "a path to a model YAML")
        model_config = load_yaml_config(model_config_path) or {}
        generator = model_config.get("generator")
        if generator not in SUPPORTED_GENERATORS:
            raise ValueError(
                f"Containerized eval execution currently supports "
                f"{sorted(SUPPORTED_GENERATORS)}, but {model_config_path} uses "
                f"generator {generator!r}. Set `containerization.enabled: false` "
                f"to run it in-process."
            )
        return generator

    @property
    def backend(self) -> ContainerBackend:
        if self._backend is None:
            with self._lock:
                if self._backend is None:
                    self._backend = acquire_shared_backend(self.containerization)
        return self._backend

    # -- main entrypoint -------------------------------------------------

    def evaluate(
        self,
        dataset: list[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ) -> tuple[list, list]:
        scenarios = self._expand_scenarios(dataset)
        if not scenarios:
            logging.warning(
                "Containerized agent evaluation found no scenarios to run.")
            return [], []

        specs = self._build_specs(scenarios, job_id, run_time)
        logging.info(
            "Dispatching %d eval case(s) to %d worker pool(s) %s "
            "(max %d concurrent)",
            len(specs), len(self.router.pools), self.containerization.pool_names,
            self.router.capacity,
        )

        eval_outputs: list[Any] = []
        scoring_results: list[Any] = []
        results = self._run_all(specs)

        for spec, result in zip(specs, results):
            if result.ok:
                eval_outputs.extend(result.agent_results)
                scoring_results.extend(result.scoring_results)
            else:
                logging.error(
                    "Eval case %s failed on worker pool %s: %s",
                    spec.case_id, result.pool, result.error)
                eval_outputs.append(
                    self._failure_row(spec, result, job_id))

        succeeded = sum(1 for r in results if r.ok)
        logging.info(
            "Containerized evaluation finished: %d/%d cases produced results",
            succeeded, len(results))
        return eval_outputs, scoring_results

    def _run_all(self, specs: list[EvalCaseSpec]) -> list[CaseResult]:
        """Runs every spec, returning results in the order the specs came in."""
        results: list[Optional[CaseResult]] = [None] * len(specs)
        workers = min(self.router.capacity, len(specs))
        try:
            with concurrent.futures.ThreadPoolExecutor(workers) as pool:
                futures = {
                    pool.submit(self._run_one, spec): index
                    for index, spec in enumerate(specs)
                }
                for future in concurrent.futures.as_completed(futures):
                    index = futures[future]
                    try:
                        results[index] = future.result()
                    except Exception as e:
                        logging.exception(
                            "Dispatching eval case %s failed", specs[index].case_id)
                        results[index] = CaseResult(
                            case_id=specs[index].case_id,
                            error=f"{type(e).__name__}: {e}",
                        )
        finally:
            if self._owns_backend and self._backend is not None:
                # Refcounted: this only closes the client once no other eval
                # session is still using it.
                release_shared_backend(self._backend)
                self._backend = None
        # Keep positional alignment with `specs`; a slot can only still be None
        # if a future neither returned nor raised, which would otherwise shift
        # every later result onto the wrong spec.
        return [
            r or CaseResult(case_id=specs[i].case_id, error="No result recorded.")
            for i, r in enumerate(results)
        ]

    def _run_one(self, spec: EvalCaseSpec) -> CaseResult:
        """Places one case on a pool, waits for it, and cleans up after it."""
        case_timeout = self._case_timeout(spec.scenario)
        deadline = self.containerization.deadline_seconds(case_timeout)

        with self.router.acquire() as pool:
            handle = self.backend.submit(spec, pool, deadline)
            try:
                # Give the backend a slightly longer client-side budget than
                # the Job deadline so a Job that Kubernetes is about to kill
                # gets reported by its own status rather than as a client
                # timeout, which carries no pod detail.
                result = self.backend.wait(
                    handle,
                    timeout_seconds=(
                        deadline
                        + self.containerization.poll_interval_seconds * 2
                    ),
                )
            except Exception as e:
                logging.exception(
                    "Waiting on container for case %s failed", spec.case_id)
                result = CaseResult(
                    case_id=spec.case_id,
                    error=f"{type(e).__name__}: {e}",
                    pool=pool.name,
                    container_ref=handle.name,
                )

            try:
                self.backend.cleanup(handle, succeeded=result.ok)
            except Exception:
                logging.warning(
                    "Failed to clean up container for case %s",
                    spec.case_id, exc_info=True)
            return result

    def _case_timeout(self, scenario: dict) -> Optional[float]:
        scenario_timeout = get_eval_case_timeout(scenario)
        return (
            scenario_timeout
            if scenario_timeout is not None
            else self.eval_case_timeout_seconds
        )

    # -- spec construction -----------------------------------------------

    @staticmethod
    def _expand_scenarios(dataset: list[EvalGeminiCliRequest]) -> list[dict]:
        """Flattens dataset items into one scenario per eval case.

        The agent dataset loader packs an entire evalset into a single request
        whose payload holds every scenario, which is why the in-process path
        runs them sequentially inside one work item. Containerized execution
        splits them back apart so each gets its own container.
        """
        scenarios: list[dict] = []
        for item in dataset:
            payload = item.payload
            if isinstance(payload, str):
                try:
                    payload = json.loads(payload)
                except json.JSONDecodeError:
                    logging.error(
                        "Skipping dataset item %s: payload is not valid JSON",
                        getattr(item, "id", "?"))
                    continue
            if not isinstance(payload, dict):
                logging.error(
                    "Skipping dataset item %s: payload is %s, expected an object",
                    getattr(item, "id", "?"), type(payload).__name__)
                continue
            if "scenarios" in payload:
                scenarios.extend(payload.get("scenarios") or [])
            else:
                scenarios.append(payload)
        return scenarios

    def _build_specs(
        self,
        scenarios: list[dict],
        job_id: str,
        run_time: datetime.datetime,
    ) -> list[EvalCaseSpec]:
        session_dir = self._session_dir()
        run_time_iso = run_time.isoformat()
        return [
            EvalCaseSpec.build(
                scenario=scenario,
                config=self.config,
                job_id=job_id,
                run_time_iso=run_time_iso,
                session_dir=session_dir,
            )
            for scenario in scenarios
        ]

    @staticmethod
    def _session_dir() -> Optional[str]:
        """Where this run's `env_files/` live, mirroring the CLI generators.

        Under the gRPC server each session gets `/tmp_sessions/<id>`; a local
        run keeps them next to the sandbox home in `.venv`.
        """
        if sys.argv and sys.argv[0].endswith("eval_server.py"):
            session_id = rpc_id_var.get()
            return os.path.join("/tmp_sessions", session_id or "default")
        local = os.path.abspath(".venv")
        return local if os.path.isdir(local) else None

    # -- failure reporting -----------------------------------------------

    def _failure_row(
        self, spec: EvalCaseSpec, result: CaseResult, job_id: str
    ) -> dict:
        """An eval_output row for a case whose container never reported.

        Shaped like `AgentEvaluator._finalize_scenario`'s output so a
        dispatch-level failure shows up as a failed case in reporting instead
        of vanishing from the results.
        """
        scenario = spec.scenario
        return {
            "eval_id": scenario.get("id"),
            "stdout": "",
            "stderr": result.error or "Container produced no result.",
            "returncode": 1,
            "timed_out": "did not finish within" in (result.error or ""),
            "prompt_generator_error": None,
            "generated_error": result.error,
            "sql_generator_error": None,
            "golden_error": None,
            "generated_sql": "skipped",
            "prompt": scenario.get("starting_prompt", ""),
            "conversation_history": json.dumps([], indent=2),
            "scenario": scenario,
            "accumulated_tools": [],
            "accumulated_skills": [],
            "job_id": job_id,
            "metadata": {
                "dialects": self.config.get("dialects", []),
                "database": self.config.get("database", "unknown"),
                "scorers": self.config.get("scorers", {}),
            },
            "fake_home": None,
            "worker_pool": result.pool,
            "container_ref": result.container_ref,
            "artifact_uri": result.artifact_uri,
        }
