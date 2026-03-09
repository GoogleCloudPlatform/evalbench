from typing import Any, List
import datetime
from work import promptgenwork
from work import sqlgeninteractwork
from work import interactsqlexecwork
from work import scorework
from work import vuserwork
from mp import mprunner
import concurrent.futures
from dataset.evalinteractinput import EvalInteractInputRequest, InteractionType
from dataset.evalinteractoutput import EvalInteractOutput
from evaluator import virtualuser
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


class InteractEvaluator:
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

        self.vuser = virtualuser.VUser(self.config, global_models, core_db)
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

        for eval_input in dataset:
            eval_output = EvalInteractOutput(eval_input)
            eval_output["job_id"] = job_id
            eval_output["run_time"] = run_time
            self.interact_loop(
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
        if db_queue:
            while not db_queue.empty():
                db = db_queue.get()
                db.close_connections()
        return eval_outputs, scoring_results

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
        max_turn = eval_output["payload"]["max_turn"]
        eval_output["step_type"] = InteractionType.INIT

        while (
            eval_output["payload"]["turn"] < max_turn
            and not eval_output["terminate_flag"]
        ):
            next_step = self.next_step(eval_output)

            if next_step == InteractionType.LLM_QUESTION_PROMPT:
                eval_output["payload"]["turn"] = eval_output["payload"]["turn"] + 1
                eval_output["step_type"] = next_step
                work = promptgenwork.SQLPromptGenWork(
                    prompt_generator, eval_output)
                eval_output = work.run()

            if next_step == InteractionType.LLM_SQLGEN:
                eval_output["step_type"] = next_step
                work = sqlgeninteractwork.SQLGenInteractWork(
                    model_generator, eval_output
                )
                eval_output = work.run()

            if next_step == InteractionType.DISAMBIGUATE:
                eval_output["step_type"] = next_step
                work = vuserwork.VUserWork(self.vuser, eval_output)
                eval_output = work.run()

            if next_step == InteractionType.SQL_EXEC:
                eval_output["step_type"] = next_step
                work = interactsqlexecwork.InteractSQLExecWork(
                    core_db, self.config, eval_output, db_queue
                )
                eval_output = work.run()

            if next_step == InteractionType.SCORE:
                eval_output["step_type"] = next_step
                work = scorework.ScorerWork(
                    self.config, eval_output, scoring_results, global_models
                )
                eval_output = work.run()

            if next_step == InteractionType.SCORE:
                truncateExecutionOutputs(
                    eval_output,
                    self.config,
                )
                eval_outputs.append(eval_output)

    def next_step(self, eval_output):
        current_step = eval_output["step_type"]
        if current_step == InteractionType.INIT:
            next_step = InteractionType.LLM_QUESTION_PROMPT

        elif current_step == InteractionType.LLM_QUESTION_PROMPT:
            next_step = InteractionType.LLM_SQLGEN

        elif current_step == InteractionType.LLM_SQLGEN:
            extracted_response, terminate_flag = check_response(
                eval_output["payload"])
            if not terminate_flag:
                next_step = InteractionType.DISAMBIGUATE
            else:
                next_step = InteractionType.SQL_EXEC

        elif current_step == InteractionType.VUSER_DECODE:
            next_step = InteractionType.LLM_QUESTION_PROMPT

        elif current_step == InteractionType.DISAMBIGUATE:
            next_step = InteractionType.LLM_QUESTION_PROMPT

        elif current_step == InteractionType.SQL_EXEC:
            next_step = InteractionType.SCORE

        elif current_step == InteractionType.SCORE:
            next_step = InteractionType.COMPLETE
            eval_output["terminate_flag"] = True

        logging.info(
            "Instance: "
            + str(eval_output["payload"]["instance_id"])
            + " Turn:"
            + str(eval_output["payload"]["turn"])
            + " Current Step:"
            + str(eval_output["step_type"])
            + " Next Step:"
            + str(next_step)
        )

        return next_step
