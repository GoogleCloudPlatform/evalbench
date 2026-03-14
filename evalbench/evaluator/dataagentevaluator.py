from typing import Any, List
import datetime
from work import promptgenwork
from work import sqlgenquerydatawork
from work import interactsqlexecwork
from work import sqlexecwork
from work import scorework
from work import dataagentvuserwork
from mp import mprunner
import concurrent.futures
from dataset.evalinteractinput import EvalInteractInputRequest, InteractionType
from dataset.evalinteractoutput import EvalInteractOutput
from evaluator import dataagentvirtualuser
from evaluator.progress_reporter import (
    record_successful_prompt_gen,
    record_successful_sql_gen,
    record_successful_sql_exec,
    record_successful_scoring,
)
from queue import Queue
from databases import DB
from util.interactutil import check_response, print_interact, write_item, read_item
from util import truncateExecutionOutputs
import logging
import json
import base64


class DataAgentEvaluator:
    def __init__(
        self,
        config,
    ):
        self.config = config
        runner_config = self.config.get("runners", {})
        self.promptgen_runners = runner_config.get("promptgen_runners", 10)
        self.sqlgen_runners = runner_config.get("sqlgen_runners", 10)
        self.vuser_runners = runner_config.get("vuser_runners", 10)
        self.sqlexec_runners = runner_config.get("sqlexec_runners", 10)
        self.scoring_runners = runner_config.get("scoring_runners", 10)

    def evaluate(
        self,
        dataset: List[EvalInteractInputRequest],
        db_queue: Queue[DB],
        prompt_generator,
        model_generator,
        job_id: str,
        run_time: datetime.datetime,
        progress_reporting,
        global_models,
        core_db,
    ):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []

        self.vuser = dataagentvirtualuser.DataAgentVUser(
            self.config, global_models, core_db
        )
        self.promptrunner = mprunner.MPRunner(self.promptgen_runners)
        self.genrunner = mprunner.MPRunner(self.sqlgen_runners)
        self.vuser_runner = mprunner.MPRunner(self.vuser_runners)
        self.sqlrunner = mprunner.MPRunner(self.sqlexec_runners)
        self.scoringrunner = mprunner.MPRunner(self.scoring_runners)
        prompt_generator.setup()
        self.promptrunner.futures.clear()
        self.genrunner.futures.clear()
        self.vuser_runner.futures.clear()
        self.sqlrunner.futures.clear()
        self.scoringrunner.futures.clear()

        with concurrent.futures.ThreadPoolExecutor(max_workers=10) as executor:
            futures = []
            # Submit tasks and collect future objects
            for eval_input in dataset:
                instance_id = eval_input.id
                eval_output = EvalInteractOutput(eval_input)
                eval_output["job_id"] = job_id
                eval_output["run_time"] = run_time
                futures.append(
                    executor.submit(
                        self.interact_loop,
                        eval_output,
                        prompt_generator,
                        model_generator,
                        progress_reporting,
                        global_models,
                        core_db,
                        db_queue,
                        eval_outputs,
                        scoring_results,
                    )
                )
            # Process results as they are completed
            for future in concurrent.futures.as_completed(futures):
                try:
                    result = future.result()
                except Exception as exc:
                    import traceback

                    print(traceback.format_exc())
                    print(f"A task generated an exception: {exc}")

        if db_queue:
            while not db_queue.empty():
                db = db_queue.get()
                db.close_connections()
        return eval_outputs, scoring_results

    def trace_conversation(self, eval_output, instance_id):
        with open(f"results/conversations_{instance_id}.txt", "a") as f:
            f.write(json.dumps(eval_output, indent=4, default=str))

    def interact_loop(
        self,
        eval_output,
        prompt_generator,
        model_generator,
        progress_reporting,
        global_models,
        core_db,
        db_queue,
        eval_outputs,
        scoring_results,
    ):
        eval_output["terminate_flag"] = False
        eval_output["preprocess_sql"] = eval_output["payload"]["preprocess_sql"]
        max_turn = eval_output["payload"]["max_turn"]
        eval_output["step_type"] = InteractionType.INIT
        next_step = InteractionType.LLM_QUESTION_PROMPT
        eval_output["payload"]["prompt"] = eval_output["payload"]["amb_user_query"]

        while (
            eval_output["payload"]["turn"] < max_turn
            and not eval_output["terminate_flag"]
        ):
            if next_step == InteractionType.LLM_QUESTION_PROMPT:
                eval_output["nl_prompt"] = eval_output["payload"]["prompt"]
                eval_output["payload"]["turn"] = eval_output["payload"]["turn"] + 1
                turn = eval_output["payload"]["turn"]
                eval_output[f"user_turn_{turn}"] = eval_output["payload"]["prompt"]
                eval_output["step_type"] = InteractionType.LLM_QUESTION_PROMPT
                work = promptgenwork.SQLPromptGenWork(
                    prompt_generator, eval_output)
                eval_output = work.run()
                if eval_output["prompt_generator_error"] is None:
                    record_successful_prompt_gen(progress_reporting)
                    next_step = InteractionType.LLM_SQLGEN
                else:
                    next_step = InteractionType.COMPLETE

            if next_step == InteractionType.LLM_SQLGEN:
                work = sqlgenquerydatawork.SQLGenQueryDataWork(
                    model_generator, eval_output
                )
                eval_output = work.run()
                if eval_output["sql_generator_error"] is None:
                    record_successful_sql_gen(progress_reporting)
                    turn = eval_output["payload"]["turn"]
                    eval_output[f"system_turn_{turn}"] = eval_output["nl_response"]
                    if "disambiguation_question" not in eval_output:
                        eval_output["disambiguation_question"] = None
                    if eval_output["disambiguation_question"] is None:
                        eval_output["terminate_flag"] = True
                        next_step = InteractionType.SQL_EXEC
                    else:
                        next_step = InteractionType.DISAMBIGUATE
                else:
                    next_step = InteractionType.COMPLETE

            if next_step == InteractionType.DISAMBIGUATE:
                eval_output["step_type"] = InteractionType.VUSER_ENCODE
                work = dataagentvuserwork.DataAgentVUserWork(
                    self.vuser, eval_output)
                eval_output = work.run()
                if eval_output["vuser_error"] is None:
                    eval_output["payload"]["prompt"] = eval_output["generated"]
                    next_step = InteractionType.LLM_QUESTION_PROMPT
                else:
                    next_step = InteractionType.COMPLETE

            if next_step == InteractionType.SQL_EXEC:
                eval_output["golden_sql"] = eval_output["payload"]["sol_sql"]
                work = sqlexecwork.SQLExecWork(
                    db_queue.get(), self.config, eval_output, db_queue
                )
                eval_output = work.run()
                record_successful_sql_exec(progress_reporting)
                next_step = InteractionType.SCORE

            if next_step == InteractionType.SCORE:
                work = scorework.ScorerWork(
                    self.config, eval_output, scoring_results, global_models
                )
                eval_output = work.run()
                record_successful_scoring(progress_reporting)
                next_step = InteractionType.COMPLETE

            if next_step == InteractionType.COMPLETE:
                self.trace_conversation(eval_output, eval_output["id"])
                payload = json.dumps(eval_output, default=str)
                payload = payload.encode("utf-8")
                payload = base64.b64encode(payload)
                eval_output["payload"] = payload.decode("utf-8")
                eval_outputs.append(eval_output)
                return eval_outputs
