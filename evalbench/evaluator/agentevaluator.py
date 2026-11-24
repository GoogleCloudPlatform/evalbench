from typing import Any, List
import copy
import datetime
import concurrent.futures
import logging
import os
import shutil
import tempfile
import time
import threading

from dataset.evalgeminicliinput import EvalGeminiCliRequest
from generators.models import get_generator
from generators.models.agent_cli import AgentCliGenerator
from generators.models.noop_agent import NoopAgentGenerator
from generators.models.passthrough import NOOPGenerator
from mp import mprunner
from util.config import get_eval_case_timeout
from work.agentgenwork import AgentGenWork
from evaluator.simulateduser import SimulatedUser
from evaluator.trialsnapshot import TrialSnapshot, check_resettable
from work.agentscorework import AgentScoreWork
from scorers.score import _construction_error_metric_name
import json
import subprocess
from typing import Dict


def _check_num_trials(value: Any, source: str) -> int:
    """Returns value. Raises ValueError unless it is an integer of 1 or more."""
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(
            f"num_trials in {source} must be an integer of 1 or more, "
            f"got {value!r}")
    return value


def _infra_error(generator: AgentCliGenerator, result: Any) -> str | None:
    """Returns an error message if the model API hit a quota or server error."""
    returncode = getattr(result, "returncode", 0)
    # 124 is a timeout, which counts as a failure.
    if returncode == 124:
        return None
    api_error = generator.model_api_error(result)
    if not api_error:
        return None
    return f"Agent infrastructure error (exit {returncode}): {api_error.strip()[-500:]}"


def _is_noop(simulated_user: SimulatedUser) -> bool:
    """Returns True if the simulated user returns an empty reply by design."""
    return isinstance(simulated_user.model_generator,
                      (NOOPGenerator, NoopAgentGenerator))


class AgentEvaluator:
    def __init__(
        self,
        config,
    ):
        self.config = config
        self.eval_case_timeout_seconds = get_eval_case_timeout(config)

        model_config_path = config.get("model_config")
        if not isinstance(model_config_path, str):
            raise ValueError(
                "AgentEvaluator requires `model_config` to be a path to a model YAML")

        global_models = {
            "lock": threading.Lock(),
            "registered_models": {},
        }
        self.generator = get_generator(global_models, model_config_path)

        if not isinstance(self.generator, AgentCliGenerator):
            raise ValueError(
                f"AgentEvaluator only supports agent CLI generators "
                f"(gemini_cli, claude_code, codex_cli, agy_cli, "
                f"agent_grpc_proxy), got {type(self.generator).__name__}"
            )
        self.agent_version = self.generator.version

        runner_config = self.config.get("runners", {})
        self.agent_runners = runner_config.get("agent_runners", 10)
        self.num_trials = _check_num_trials(
            self.config.get("num_trials", 1), "the run config")
        # Set if the job stops before all trials ran.
        self.incomplete_reason: str | None = None

    def evaluate(
        self,
        dataset: List[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        if isinstance(self.generator, AgentCliGenerator):
            return self._evaluate_agent_cli(dataset, job_id, run_time)
        else:
            raise NotImplementedError(
                "This evaluator currently only supports GeminiCliGenerator, ClaudeCodeGenerator, CodexCliGenerator and AgyCliGenerator")

    def _evaluate_agent_cli(
        self,
        dataset: List[EvalGeminiCliRequest],
        job_id: str,
        run_time: datetime.datetime,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        generator_name = type(self.generator).__name__
        logging.info(f"Running {generator_name} evaluation")
        if self.num_trials > 1:
            logging.warning(
                "Cloud resources are not reset between trials. Set "
                "num_trials: 1 on scenarios that create or change them.")

        # Extract generic metadata
        metadata = {
            "dialects": self.config.get("dialects", []),
            "database": self.config.get("database", "unknown"),
            "scorers": self.config.get("scorers", {}),
        }

        self.agentrunner = mprunner.MPRunner(self.agent_runners)
        snapshot = None
        try:
            # Trials run one after another, and each trial runs the full
            # suite. They share the generator's fake_home and each scenario's
            # work_dir, so concurrent trials would write to the same files.
            # The snapshot puts these directories back before each trial
            # after the first, so a trial does not see the files of an
            # earlier one.
            if self.num_trials > 1:
                snapshot = TrialSnapshot(
                    getattr(self.generator, "fake_home", None),
                    self._work_dirs_to_reset(dataset))
                snapshot.save()
            for trial_index in range(self.num_trials):
                if trial_index > 0:
                    try:
                        snapshot.restore()
                    except OSError as e:
                        # Stop, because a trial must not start with the files
                        # of an earlier one. The finished trials keep their
                        # results.
                        self.incomplete_reason = (
                            f"The reset before trial {trial_index} failed: "
                            f"{e}. The remaining trials did not run.")
                        # A reset that fails half way leaves a directory with
                        # a part of its files. The snapshot has all of them.
                        kept = snapshot.keep()
                        if kept:
                            self.incomplete_reason += (
                                f" The snapshot of the sandbox home and the "
                                f"work dirs is kept at {kept}.")
                        logging.error(self.incomplete_reason)
                        break
                items = [self._trial_item(item, trial_index) for item in dataset]
                outputs, scores = self._run_items(
                    [item for item in items if item is not None],
                    job_id, metadata)
                eval_outputs.extend(outputs)
                scoring_results.extend(scores)

            return eval_outputs, scoring_results
        finally:
            self.agentrunner.shutdown()
            if snapshot is not None:
                snapshot.remove()

    def _work_dirs_to_reset(self, dataset: List[Any]) -> List[str]:
        """Returns the work dirs of the scenarios that run in more than one trial.

        Raises ValueError if the reset of a work dir would delete a directory
        that must stay.
        """
        # A reset deletes everything in a work dir, so a work dir must not be
        # one of these directories or contain one.
        protected = [os.path.expanduser("~"), os.getcwd(), tempfile.gettempdir()]
        fake_home = getattr(self.generator, "fake_home", None)
        if fake_home:
            protected.append(fake_home)
        dataset_config = self.config.get("dataset_config")
        if isinstance(dataset_config, dict):
            # The run config also accepts `dataset_config: {prompts_file: ...}`.
            dataset_config = dataset_config.get("prompts_file")
        if isinstance(dataset_config, str):
            # A work_dir of "." is the directory of the evalset file.
            protected.append(os.path.dirname(os.path.abspath(dataset_config)))

        work_dirs = []
        for item in dataset:
            eval_set = json.loads(item.payload)
            for scenario in eval_set.get("scenarios", [eval_set]):
                work_dir = scenario.get("resolved_work_dir")
                if work_dir and self._scenario_num_trials(scenario) > 1:
                    check_resettable(scenario["id"], work_dir, protected)
                    work_dirs.append(work_dir)
        return work_dirs

    def _scenario_num_trials(self, scenario: Dict[str, Any]) -> int:
        """Returns the scenario's own num_trials, or the run value if it has none."""
        return _check_num_trials(
            scenario.get("num_trials", self.num_trials),
            f"scenario {scenario.get('id')}")

    def _run_items(
        self, items: List[Any], job_id: str, metadata: Dict[str, Any]
    ) -> tuple[List[Any], List[Any]]:
        """Runs the items concurrently and returns their eval and score rows."""
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []
        first_future = len(self.agentrunner.futures)
        for item in items:
            simulated_user = SimulatedUser(self.config)
            work = AgentGenWork(
                processor=self.process_scenario,
                eval_result=item,
                job_id=job_id,
                metadata=metadata,
                simulated_user=simulated_user
            )
            self.agentrunner.execute_work(work)

        for future in concurrent.futures.as_completed(
                self.agentrunner.futures[first_future:]):
            try:
                item = future.result()
            except Exception as e:
                logging.exception(f"Agent eval case failed: {e}")
                continue

            if hasattr(item, "agent_results"):
                eval_outputs.extend(item.agent_results)
            if hasattr(item, "scoring_results"):
                scoring_results.extend(item.scoring_results)

        return eval_outputs, scoring_results

    def _trial_item(self, item: Any, trial_index: int) -> Any:
        """Returns the item for one trial, or None if no scenario runs in it.

        Scenario ids become `<id>_trial_<k>`. With one trial, the item is unchanged.
        """
        eval_set = json.loads(item.payload)
        scenarios = eval_set.get("scenarios", [eval_set])
        # Check the scenario values with one trial too. A bad value must not
        # start to fail the job when the run value changes.
        trials_of_scenarios = [
            self._scenario_num_trials(scenario) for scenario in scenarios]
        if self.num_trials == 1:
            return item

        trial_scenarios = []
        for scenario, scenario_trials in zip(scenarios, trials_of_scenarios):
            if scenario_trials > self.num_trials and trial_index == 0:
                logging.warning(
                    "Scenario %s sets num_trials=%s, above the run value %s. "
                    "Using %s.", scenario["id"], scenario_trials,
                    self.num_trials, self.num_trials)
            if trial_index >= scenario_trials:
                continue
            scenario["prompt_id"] = scenario["id"]
            scenario["trial_index"] = trial_index
            scenario["id"] = f"{scenario['id']}_trial_{trial_index}"
            trial_scenarios.append(scenario)

        if not trial_scenarios:
            return None
        if "scenarios" in eval_set:
            eval_set["scenarios"] = trial_scenarios
        else:
            eval_set = trial_scenarios[0]

        trial_item = copy.copy(item)
        trial_item.payload = json.dumps(eval_set)
        # Each trial collects its own results.
        trial_item.agent_results = []
        trial_item.scoring_results = []
        return trial_item

    def process_scenario(
        self,
        scenario: Dict[str, Any],
        eval_result: Any,
        job_id: str,
        metadata: Dict[str, Any],
        simulated_user: Any = None
    ):
        """Processes a single scenario."""
        current_prompt = scenario["starting_prompt"]
        env = scenario.get("env", {})
        max_turns = scenario.get("max_turns", 1)
        conversation_plan = scenario.get("conversation_plan", "")
        conversation_history = []
        accumulated_tools = []
        accumulated_skills = []
        last_result = None

        resolved_work_dir = scenario.get("resolved_work_dir")
        if resolved_work_dir:
            os.makedirs(resolved_work_dir, exist_ok=True)

        # Copy declared env_files to fake_home
        fake_home = getattr(self.generator, "fake_home", None)
        if fake_home:
            session_dir = os.path.dirname(fake_home)
            env_files = scenario.get("env_files", [])
            for env_file in env_files:
                src_path = os.path.join(session_dir, "env_files", env_file)
                dest_path = os.path.join(fake_home, env_file)
                if os.path.exists(src_path):
                    os.makedirs(os.path.dirname(dest_path), exist_ok=True)
                    shutil.copy2(src_path, dest_path)
                    logging.info(
                        "Natively copied env file to sandbox: %s", dest_path
                    )
                else:
                    logging.warning(
                        "Declared env file not found in session: %s", src_path
                    )

        session_id = None

        # Dynamically calculate the effective timeout, taking into account
        # both the scenario timeout and the global timeout.
        start_time = time.monotonic()
        scenario_timeout = get_eval_case_timeout(scenario)
        effective_case_timeout_seconds = (
            scenario_timeout
            if scenario_timeout is not None
            else self.eval_case_timeout_seconds
        )
        last_result = None
        generated_error = None

        for turn in range(max_turns):
            remaining_timeout_seconds = None
            if effective_case_timeout_seconds is not None:
                elapsed_seconds = time.monotonic() - start_time
                remaining_timeout_seconds = effective_case_timeout_seconds - elapsed_seconds
                if remaining_timeout_seconds <= 0:
                    logging.warning(
                        f"Eval case timeout ({effective_case_timeout_seconds}s) reached before turn {turn + 1}."
                    )
                    timeout_msg = f"TimeoutError: Scenario timed out after {effective_case_timeout_seconds}s before turn {turn + 1}"
                    if last_result is None:
                        last_result = subprocess.CompletedProcess(
                            args=[self.agent_version],
                            returncode=124,
                            stdout="",
                            stderr=timeout_msg,
                        )
                    else:
                        current_stderr = getattr(last_result, "stderr", "") or ""
                        new_stderr = f"{current_stderr}\n{timeout_msg}".strip()
                        last_result = subprocess.CompletedProcess(
                            args=getattr(last_result, "args", [self.agent_version]),
                            returncode=124,
                            stdout=getattr(last_result, "stdout", "") or "",
                            stderr=new_stderr,
                        )
                    break

            logging.info(
                f"Turn {turn + 1}/{max_turns} - Prompt: {current_prompt}")
            if isinstance(self.generator, AgentCliGenerator):
                cli_cmd = self.generator.create_command(
                    cli=self.agent_version,
                    prompt=current_prompt,
                    env=env,
                    resume=(turn > 0),
                    session_id=session_id,
                    cwd=resolved_work_dir,
                )
                try:
                    result = self.generator.safe_generate(
                        cli_cmd, timeout_seconds=remaining_timeout_seconds
                    )
                    if result.stdout:
                        parsed = self.generator.parse_response(result.stdout)
                        if parsed.get("session_id"):
                            session_id = parsed["session_id"]
                except Exception as e:
                    logging.error(f'CLI execution failed: {e}')
                    result = subprocess.CompletedProcess(
                        args=[self.agent_version], returncode=1, stdout='', stderr=str(e)
                    )
            else:
                try:
                    result = self.generator.generate(current_prompt)
                except Exception as e:
                    logging.error(f'LLM generation failed: {e}')
                    result = str(e)

            last_result = result

            self._log_cli_result(turn, max_turns, result)

            tools = []
            if isinstance(self.generator, AgentCliGenerator):
                tools = self.generator.extract_tools(result.stdout)
            accumulated_tools.extend(tools)

            # Extract skills from generator output
            if isinstance(self.generator, AgentCliGenerator):
                skills = self.generator.extract_skills(result.stdout)
                accumulated_skills.extend(skills)

            conversation_history.append({
                "user": current_prompt,
                "agent": result.stdout
            })

            generated_error = _infra_error(self.generator, result)
            if generated_error:
                logging.error(generated_error)
                break

            if turn < max_turns - 1:
                if simulated_user:
                    try:
                        next_response = simulated_user.get_next_response(
                            conversation_plan,
                            conversation_history,
                            result.stdout
                        )
                        # QueryGenerator.generate returns "" when out of quota.
                        # ClaudeGenerator returns None on any error.
                        if (not str(next_response or "").strip()
                                and not _is_noop(simulated_user)):
                            raise RuntimeError(
                                "empty reply from the simulated user model")
                    except Exception as e:
                        generated_error = f"Simulated user error: {e}"
                        logging.error(generated_error)
                        break
                    if "TERMINATE" in next_response:
                        logging.info("Simulated user terminated conversation.")
                        break
                    current_prompt = next_response
                else:
                    break

        if last_result is None:
            last_result = subprocess.CompletedProcess(
                args=[self.agent_version], returncode=0, stdout="", stderr=""
            )

        self._finalize_scenario(
            scenario,
            last_result,
            conversation_history,
            accumulated_tools,
            accumulated_skills,
            eval_result,
            job_id,
            metadata,
            generated_error,
        )

    def _log_cli_result(self, turn: int, max_turns: int, result: subprocess.CompletedProcess):
        generator_name = self.generator.name
        logging.info(
            f"Turn {turn + 1}/{max_turns} - {generator_name} exit code: {result.returncode}")
        logging.info(
            f"Turn {turn + 1}/{max_turns} - {generator_name} stdout: {result.stdout}")
        logging.info(
            f"Turn {turn + 1}/{max_turns} - {generator_name} stderr: {result.stderr}")

    def _finalize_scenario(
        self,
        scenario: Dict[str, Any],
        last_result: subprocess.CompletedProcess,
        conversation_history: List[Dict[str, str]],
        accumulated_tools: List[str],
        accumulated_skills: List[str],
        eval_result: Any,
        job_id: str,
        metadata: Dict[str, Any],
        generated_error: str | None = None,
    ):
        """Finalizes the scenario by scoring and appending results."""
        timed_out = (
            getattr(last_result, "returncode", 0) == 124
            or "TimeoutError" in (getattr(last_result, "stderr", "") or "")
        )

        # Prepare intermediate eval_output with all necessary data for scoring
        eval_output_data = {
            "eval_id": scenario["id"],
            "stdout": getattr(last_result, "stdout", "") or "",
            "stderr": getattr(last_result, "stderr", "") or "",
            "returncode": getattr(last_result, "returncode", 0),
            "timed_out": timed_out,
            "prompt_generator_error": None,
            "generated_error": generated_error,
            "sql_generator_error": None,
            "golden_error": None,
            "generated_sql": "skipped",
            "prompt": scenario["starting_prompt"],
            "conversation_history": json.dumps(conversation_history, indent=2),
            "scenario": scenario,
            "accumulated_tools": accumulated_tools,
            "accumulated_skills": accumulated_skills,
            "job_id": job_id,
            "metadata": metadata,
            "fake_home": self.generator.fake_home if hasattr(self.generator, "fake_home") else None
        }
        if "trial_index" in scenario:
            eval_output_data["prompt_id"] = scenario["prompt_id"]
            eval_output_data["trial_index"] = scenario["trial_index"]

        # Execution evidence must survive a failed scorer. In normal scorer
        # failures, score.compare creates an error-bearing comparison row.
        eval_result.agent_results.append(eval_output_data)
        # Stage this scenario's scores: a late dispatch exception must never
        # publish success rows from an incompletely scored scenario.
        staged_scenario_scores: list[dict] = []
        try:
            score_work = AgentScoreWork(
                config=self.config,
                eval_output=eval_output_data,
                scoring_results=staged_scenario_scores
            )
            score_work.run()
        except Exception as exc:
            # Keep the completed execution evidence, but do not publish the
            # partial scenario buffer or touch any other scenario's scores.
            eval_output_data["scoring_error"] = f"{type(exc).__name__}: {exc}"
            logging.exception("Unexpected scoring failure for scenario %s",
                              scenario["id"])
            # Publish an *unscored* comparison-error record for each configured
            # metric. The analyzer consumes scoring_results, not agent_results;
            # without these records an otherwise completed agent execution is
            # incorrectly absent from execution accounting. Do not publish
            # partially staged successes from the interrupted dispatch.
            for scorer_name, scorer_config in self.config.get("scorers", {}).items():
                error_row = {
                    "id": scenario["id"],
                    "comparator": _construction_error_metric_name(
                        scorer_name, scorer_config),
                    "score": 0,
                    "generated_sql": eval_output_data["generated_sql"],
                    "generated_error": eval_output_data["generated_error"],
                    "comparison_error": eval_output_data["scoring_error"],
                    "comparison_logs": None,
                    "dialects": metadata.get("dialects", []),
                    "database": metadata.get("database", "unknown"),
                    "job_id": job_id,
                }
                if "trial_index" in scenario:
                    error_row["prompt_id"] = scenario["prompt_id"]
                    error_row["trial_index"] = scenario["trial_index"]
                eval_result.scoring_results.append(error_row)
        else:
            eval_result.scoring_results.extend(staged_scenario_scores)
