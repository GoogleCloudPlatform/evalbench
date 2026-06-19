"""Orchestrator for MCP compliance-check runs.

Mirrors AgentOrchestrator: holds the job_id/run_time, runs the evaluator
once per dataset item, and exposes process() that dumps eval_outputs +
scoring_results to temp JSON files for the reporting layer.
"""

import datetime
import json
import logging
import tempfile
import uuid

from dataset.evalmcpendpointinput import EvalMcpEndpointRequest
from evaluator.compliancecheckevaluator import ComplianceCheckEvaluator
from evaluator.orchestrator import Orchestrator


class ComplianceCheckOrchestrator(Orchestrator):
    def __init__(
        self,
        config,
        db_configs,
        setup_config,
        report_progress=False,
    ):
        self.config = config
        self.db_configs = db_configs
        self.setup_config = setup_config
        self.job_id = f"{uuid.uuid4()}"
        self.run_time = datetime.datetime.now()
        self.total_eval_outputs = []
        self.total_scoring_results = []
        self.reporting_total_evals_done = 0
        self.report_progress = report_progress

    def evaluate(self, dataset: list[EvalMcpEndpointRequest]):
        logging.info("Starting MCP compliance-check evaluation")
        evaluator = ComplianceCheckEvaluator(self.config)
        eval_outputs, scoring_results = evaluator.evaluate(
            dataset, self.job_id, self.run_time
        )
        self.total_eval_outputs.extend(eval_outputs)
        self.total_scoring_results.extend(scoring_results)

    def process(self):
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            json.dump(
                self.total_eval_outputs, f, sort_keys=True, indent=4, default=str
            )
            results_tf = f.name
        with tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".json") as f:
            json.dump(
                self.total_scoring_results, f, sort_keys=True, indent=4, default=str
            )
            scores_tf = f.name
        return (
            self.job_id,
            self.run_time,
            results_tf,
            scores_tf,
            None,
        )
