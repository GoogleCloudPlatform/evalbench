from typing import Any, List
from util import printProgressBar, truncateExecutionOutputs
from work import promptgenwork
from work import sqlgenwork
from work import sqlexecwork
from work import scorework
from mp import mprunner
import concurrent.futures
from dataset.evalinput import EvalInputRequest
from dataset.evaloutput import EvalOutput

NUM_WORKERS = 10

class Evaluator:
    def __init__(self, job_id, run_time, experiment_config, prompt_generator, model_generator, db_queue):
        self.job_id = job_id
        self.run_time = run_time
        self.eval_ids = None
        self.experiment_config = experiment_config
        self.prompt_generator = prompt_generator
        self.model_generator = model_generator
        self.db_queue = db_queue

    def evaluate(self, dataset: List[EvalInputRequest], dataset_len: int):
        eval_outputs: List[Any] = []
        scoring_results: List[Any] = []

        self.promptrunner = mprunner.MPRunner(NUM_WORKERS)
        self.genrunner = mprunner.MPRunner(NUM_WORKERS)
        self.sqlrunner = mprunner.MPRunner(NUM_WORKERS)
        self.scoringrunner = mprunner.MPRunner(NUM_WORKERS)

        prompt_i = 0
        gen_i = 0
        exec_i = 0
        score_i = 0

        self.promptrunner.futures.clear()
        self.genrunner.futures.clear()
        self.sqlrunner.futures.clear()
        self.scoringrunner.futures.clear()

        for eval_input in dataset:
            eval_output = EvalOutput(eval_input)
            eval_output["job_id"] = self.job_id
            eval_output["run_time"] = self.run_time
            work = promptgenwork.SQLPromptGenWork(self.prompt_generator, eval_output)
            self.promptrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.promptrunner.futures):
            eval_output = future.result()
            prompt_i = prompt_i + 1
            printProgressBar(
                prompt_i, dataset_len, prefix="Prompts:", suffix="Complete", length=50
            )
            work = sqlgenwork.SQLGenWork(self.model_generator, eval_output)
            self.genrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.genrunner.futures):
            eval_output = future.result()
            gen_i = gen_i + 1
            printProgressBar(
                gen_i, dataset_len, prefix="SQLGen:", suffix="Complete", length=50
            )
            work = sqlexecwork.SQLExecWork(self.db_queue.get(), self.experiment_config, eval_output)
            self.sqlrunner.execute_work(work)

        for future in concurrent.futures.as_completed(self.sqlrunner.futures):
            eval_output = future.result()
            exec_i = exec_i + 1
            work = scorework.ScorerWork(
                self.experiment_config, eval_output, scoring_results
            )
            self.scoringrunner.execute_work(work)
            printProgressBar(
                exec_i, dataset_len, prefix="SQLExec:", suffix="Complete", length=50
            )

        for future in concurrent.futures.as_completed(self.scoringrunner.futures):
            eval_output = future.result()
            score_i = score_i + 1
            if "truncate_execution_outputs" in self.experiment_config:
                truncateExecutionOutputs(
                    eval_output,
                    self.experiment_config["truncate_execution_outputs"],
                )
            printProgressBar(
                score_i,
                dataset_len,
                prefix="Scoring:",
                suffix="Complete",
                length=50,
            )
            eval_outputs.append(eval_output)
       
        return eval_outputs, scoring_results
